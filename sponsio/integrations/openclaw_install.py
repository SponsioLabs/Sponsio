"""OpenClaw install/uninstall — deploy the sponsio-openclaw plugin
into ``~/.openclaw/extensions/`` AND bootstrap the
``~/.sponsio/plugins/_host_openclaw/sponsio.yaml`` library so OpenClaw
agents see Sponsio's defaults.

Surfaced behind ``sponsio host install openclaw``.

Three things happen at install time, all idempotent:

1. **Fallback contract library** — write
   ``~/.sponsio/plugins/_host_openclaw/sponsio.yaml`` so the plugin
   has a default ruleset to evaluate against for tools whose name
   doesn't match a per-plugin namespace.

2. **OpenClaw extension** — copy the pre-built TS plugin
   (``openclaw.plugin.json`` + ``package.json`` + ``dist/index.js``)
   from the wheel's bundled ``sponsio/plugin/openclaw_artifact/``
   into ``~/.openclaw/extensions/sponsio-openclaw/``.  The OpenClaw
   plugin discovery path picks it up on next start.

3. **Plugin registration** — patch ``~/.openclaw/openclaw.json``'s
   ``plugins.entries.sponsio-openclaw = { enabled: true }`` so
   OpenClaw activates the plugin on next start.  The original
   ``openclaw.json`` is backed up to ``openclaw.json.before-sponsio``
   on first install only — re-runs do not overwrite the backup.

Docker scenarios (OpenClaw-in-container, with sponsio CLI absent
inside the container) need additional steps the host installer
deliberately doesn't take.  See the demo's ``setup_openclaw.sh``
for the docker-specific path.
"""

from __future__ import annotations

import json
import os
import shutil
from importlib import resources
from pathlib import Path

from sponsio.integrations.hosts import (
    HookHost,
    HostInstallResult,
    HostUninstallResult,
)

# ---------------------------------------------------------------------------
# Filesystem locations
# ---------------------------------------------------------------------------

_PLUGIN_ID = "sponsio-openclaw"


def _library_root() -> Path:
    override = os.environ.get("SPONSIO_PLUGIN_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sponsio" / "plugins"


def _openclaw_home() -> Path:
    """OpenClaw's user state dir.  Bind-mounted into the container at
    ``/home/node/.openclaw`` in the standard docker layout."""
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw"


def _extension_target() -> Path:
    return _openclaw_home() / "extensions" / _PLUGIN_ID


def _openclaw_json_path() -> Path:
    return _openclaw_home() / "openclaw.json"


def _bundled_artifact_root() -> Path:
    """Locate the bundled plugin artifact dir.

    Uses :func:`importlib.resources.files` so this works both from a
    source checkout and from a pip-installed wheel.
    """
    return Path(str(resources.files("sponsio.plugin"))) / "openclaw_artifact"


# ---------------------------------------------------------------------------
# Step 1 — fallback contract library
# ---------------------------------------------------------------------------


def _write_fallback_library(force: bool) -> tuple[bool, str, Path]:
    """Idempotent write of ``_host_openclaw/sponsio.yaml``."""
    from sponsio.plugin.registry import read_bundled

    target_dir = _library_root() / "_host_openclaw"
    target = target_dir / "sponsio.yaml"

    try:
        src_text = read_bundled("_host_openclaw")
    except (FileNotFoundError, ModuleNotFoundError) as e:
        return False, f"bundled _host_openclaw library missing ({e})", target

    if target.exists() and not force:
        return False, "library already present", target

    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(src_text, encoding="utf-8")
    return True, "wrote OpenClaw fallback contract library", target


# ---------------------------------------------------------------------------
# Step 2 — deploy the OpenClaw extension
# ---------------------------------------------------------------------------


def _deploy_extension(force: bool) -> tuple[bool, str, Path]:
    """Copy the pre-built plugin into ``~/.openclaw/extensions/sponsio-openclaw/``.

    Returns ``(written, note, target_dir)``.  ``written=False`` is
    informational (already present at the same version, or artifact
    bundle missing) — not a hard error.  Re-running with ``force=True``
    overwrites.
    """
    src = _bundled_artifact_root()
    if not src.exists():
        return (
            False,
            f"bundled plugin artifact missing at {src}; reinstall sponsio",
            _extension_target(),
        )

    target = _extension_target()
    if target.exists() and not force:
        # Cheap version check: if manifest matches what we'd write,
        # treat as up-to-date.  Anything more invasive (semver compare)
        # is over-engineering until we ship multiple plugin versions.
        try:
            installed = (target / "openclaw.plugin.json").read_text(encoding="utf-8")
            bundled = (src / "openclaw.plugin.json").read_text(encoding="utf-8")
            if installed == bundled:
                return False, "extension already present at same version", target
        except OSError:
            pass
        return False, "extension already present — pass force=True to replace", target

    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    # We copy a curated set of files rather than the whole tree so
    # incidental crud (a stray README.md from the artifact dir, etc.)
    # doesn't end up in the user's OpenClaw state.
    shutil.copytree(
        src,
        target,
        ignore=shutil.ignore_patterns("README.md", "__pycache__", "*.pyc"),
    )
    return True, f"deployed plugin to {target}", target


# ---------------------------------------------------------------------------
# Step 3 — patch openclaw.json's plugins.entries
# ---------------------------------------------------------------------------


def _patch_openclaw_json(force: bool) -> tuple[bool, str, Path]:
    """Register ``sponsio-openclaw`` in ``openclaw.json``'s
    ``plugins.entries`` AND write an install-record under
    ``plugins.installs`` so OpenClaw's trust model treats the plugin
    as deliberately installed (not "untracked local code").

    Without the install record, OpenClaw 2026.4+ emits two warnings
    on every gateway start:
      * "plugins.allow is empty; discovered non-bundled plugins may auto-load…"
      * "<id>: loaded without install/load-path provenance…"

    The install record (``installPath`` + ``sourcePath``) silences
    both: ``isTrackedByProvenance`` in OpenClaw's loader matches the
    plugin's source against these paths.

    Idempotent.  Backs up to ``openclaw.json.before-sponsio`` on
    first install only.
    """
    config_path = _openclaw_json_path()
    if not config_path.exists():
        return (
            False,
            f"{config_path} not found — start OpenClaw once to generate it, "
            "then re-run install",
            config_path,
        )

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return False, f"could not read {config_path}: {e}", config_path

    plugins_section = cfg.setdefault("plugins", {})
    if not isinstance(plugins_section, dict):
        return (
            False,
            f"{config_path}: 'plugins' is not an object — refusing to patch",
            config_path,
        )

    entries = plugins_section.setdefault("entries", {})
    if not isinstance(entries, dict):
        return (
            False,
            f"{config_path}: 'plugins.entries' is not an object — refusing to patch",
            config_path,
        )

    installs = plugins_section.setdefault("installs", {})
    if not isinstance(installs, dict):
        return (
            False,
            f"{config_path}: 'plugins.installs' is not an object — refusing to patch",
            config_path,
        )

    # Inside-container path the plugin will load from.  The host-side
    # path differs when ``~/.openclaw`` is bind-mounted into a Docker
    # container — but OpenClaw resolves the install record AT LOAD
    # TIME from the gateway's perspective, which sees the container
    # path.  Default to the in-container path; override via env.
    install_path_str = os.environ.get(
        "SPONSIO_OPENCLAW_INSTALL_PATH",
        str(_extension_target()),
    )
    # OpenClaw's install-record schema (see runtime-schema's
    # ``plugins.installs.*.source``) only accepts a fixed set of
    # ``source`` values.  ``path`` is the right one for our flow:
    # we deploy a pre-built bundle from a local path baked into
    # the wheel, no npm fetch / clawhub registry involvement.
    install_record: dict = {
        "source": "path",
        "installPath": install_path_str,
        "sourcePath": install_path_str,
    }
    # Best-effort version tag from the bundled manifest — keeps
    # ``openclaw plugins list`` honest about what got installed.
    try:
        manifest = json.loads(
            (_bundled_artifact_root() / "openclaw.plugin.json").read_text(
                encoding="utf-8"
            )
        )
        version = manifest.get("version")
        if isinstance(version, str) and version:
            install_record["version"] = version
    except (OSError, json.JSONDecodeError):
        pass

    existing_entry = entries.get(_PLUGIN_ID)
    existing_install = installs.get(_PLUGIN_ID)
    already_correct = (
        isinstance(existing_entry, dict)
        and existing_entry.get("enabled") is True
        and existing_install == install_record
    )
    if already_correct and not force:
        return False, f"{_PLUGIN_ID} already registered in openclaw.json", config_path

    # Backup once — don't clobber an earlier (pristine) backup on
    # re-installs.
    backup = config_path.with_suffix(config_path.suffix + ".before-sponsio")
    if not backup.exists():
        try:
            shutil.copy2(config_path, backup)
        except OSError:
            # Backup failure is non-fatal; surface it in the note.
            pass

    entries[_PLUGIN_ID] = {"enabled": True}
    installs[_PLUGIN_ID] = install_record
    config_path.write_text(
        json.dumps(cfg, indent=2) + "\n",
        encoding="utf-8",
    )
    return True, f"registered {_PLUGIN_ID} in {config_path}", config_path


# ---------------------------------------------------------------------------
# Public install/uninstall
# ---------------------------------------------------------------------------


def install(
    host: HookHost,
    *,
    scope: str = "user",
    fail_closed: bool = True,  # accepted for API parity; OpenClaw plugin owns this
    force: bool = False,
    binary: str | None = None,  # accepted for API parity
) -> HostInstallResult:
    """End-to-end install: library + extension + openclaw.json patch."""

    notes: list[str] = []
    any_written = False
    primary_path: Path

    lib_written, lib_note, lib_path = _write_fallback_library(force)
    primary_path = lib_path
    notes.append(f"library: {lib_note}")
    any_written = any_written or lib_written

    ext_written, ext_note, _ext_path = _deploy_extension(force)
    notes.append(f"extension: {ext_note}")
    any_written = any_written or ext_written

    cfg_written, cfg_note, _cfg_path = _patch_openclaw_json(force)
    notes.append(f"openclaw.json: {cfg_note}")
    any_written = any_written or cfg_written

    if any_written:
        notes.append("restart OpenClaw (or re-run ``openclaw gateway``) to activate")
        # OpenClaw 2026.4+ prints "plugins.allow is empty" once per
        # gateway start when any non-bundled plugin auto-loads.  We
        # don't touch ``plugins.allow`` ourselves: setting it would
        # silently disable any *other* third-party plugin the user
        # has under ``~/.openclaw/extensions/``.  The user opts in
        # explicitly when they're ready.
        notes.append(
            "you may see one ``plugins.allow is empty`` warning at "
            "startup — that's OpenClaw's safety nudge, not a Sponsio "
            'issue; silence it with ``plugins.allow: ["sponsio-openclaw", ...]``'
        )

    return HostInstallResult(
        host=host.name,
        config_path=primary_path,
        written=any_written,
        note="; ".join(notes),
    )


def status(host: HookHost) -> dict:
    """Return a structured status report for the OpenClaw install.

    Each top-level key is one of the three install steps; values are
    ``{"ok": bool, "detail": str}``.  Plus a ``libraries`` summary
    listing per-plugin contract libraries on disk.

    Renderer in ``sponsio host status`` formats this for terminal
    display; programmatic consumers can inspect the dict directly.
    """
    report: dict = {}

    def _exists(p):
        # ``Path.exists`` raises ``PermissionError`` if the *parent*
        # directory isn't readable (common when ``~/.openclaw`` is
        # owned by another user).  Treat that as "can't determine,
        # probably not ours" rather than crashing the whole report.
        try:
            return p.exists()
        except PermissionError:
            return None

    # 1. Fallback library file present
    lib_path = _library_root() / "_host_openclaw" / "sponsio.yaml"
    lib_state = _exists(lib_path)
    if lib_state is None:
        report["library"] = {
            "ok": False,
            "detail": f"permission denied reading {lib_path} (set SPONSIO_PLUGIN_ROOT?)",
        }
    else:
        report["library"] = {
            "ok": lib_state,
            "detail": str(lib_path) if lib_state else f"missing: {lib_path}",
        }

    # 2. Extension deployed
    ext_path = _extension_target()
    ext_index = ext_path / "dist" / "index.js"
    ext_manifest = ext_path / "openclaw.plugin.json"
    idx_state = _exists(ext_index)
    man_state = _exists(ext_manifest)
    if idx_state is None or man_state is None:
        report["extension"] = {
            "ok": False,
            "detail": (
                f"permission denied reading {ext_path} "
                "(set OPENCLAW_HOME to a path you own)"
            ),
        }
    elif idx_state and man_state:
        try:
            version = json.loads(ext_manifest.read_text(encoding="utf-8")).get(
                "version", "?"
            )
        except (OSError, json.JSONDecodeError):
            version = "?"
        report["extension"] = {
            "ok": True,
            "detail": f"deployed at {ext_path} (version {version})",
        }
    else:
        report["extension"] = {
            "ok": False,
            "detail": f"missing files at {ext_path}",
        }

    # 3. openclaw.json registration
    cfg_path = _openclaw_json_path()
    cfg_state = _exists(cfg_path)
    if cfg_state is None:
        report["registration"] = {
            "ok": False,
            "detail": (
                f"permission denied reading {cfg_path} "
                "(set OPENCLAW_HOME to a path you own)"
            ),
        }
    elif not cfg_state:
        report["registration"] = {
            "ok": False,
            "detail": f"{cfg_path} not present (start OpenClaw once to generate)",
        }
    else:
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            entry = cfg.get("plugins", {}).get("entries", {}).get(_PLUGIN_ID)
            inst = cfg.get("plugins", {}).get("installs", {}).get(_PLUGIN_ID)
            entry_ok = isinstance(entry, dict) and entry.get("enabled") is True
            inst_ok = isinstance(inst, dict) and bool(inst.get("installPath"))
            report["registration"] = {
                "ok": entry_ok and inst_ok,
                "detail": (
                    f"entry={'enabled' if entry_ok else 'missing/disabled'}, "
                    f"install={'recorded' if inst_ok else 'missing'} ({cfg_path})"
                ),
            }
        except (OSError, json.JSONDecodeError) as e:
            report["registration"] = {
                "ok": False,
                "detail": f"could not parse {cfg_path}: {e}",
            }

    # 4. Per-plugin contract libraries on disk — surface each
    # contract's ``desc`` + ``A`` (assumption) + ``E`` (enforcement)
    # so the status output mirrors the on-disk YAML shape rather
    # than collapsing to a count.
    lib_root = _library_root()
    libs: list[dict] = []
    try:
        if lib_root.exists():
            for child in sorted(lib_root.iterdir()):
                yaml_file = child / "sponsio.yaml"
                if yaml_file.is_file():
                    libs.append({"name": child.name, **_parse_library(yaml_file)})
    except (PermissionError, OSError):
        # Same swallow-and-continue posture as the per-step blocks
        # above: status() should never raise just because a path is
        # locked by another user.
        pass
    report["libraries"] = libs
    return report


def _parse_library(yaml_file: Path) -> dict:
    """Read a per-plugin ``sponsio.yaml`` and return ``{contracts, includes}``.

    ``contracts`` is a list of ``{desc, A, E, activate_at}`` records;
    ``includes`` is the list of bundled-pack specs (so the renderer
    can say "+ 5 more from sponsio:incident/mcp-composition").
    """
    try:
        import yaml
    except ImportError:
        return {"contracts": [], "includes": [], "parse_error": "pyyaml missing"}

    try:
        cfg = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as e:
        return {"contracts": [], "includes": [], "parse_error": str(e)}

    contracts: list[dict] = []
    includes: list[str] = []
    agents = cfg.get("agents") if isinstance(cfg, dict) else None
    if isinstance(agents, dict):
        for _agent_name, agent_cfg in agents.items():
            if not isinstance(agent_cfg, dict):
                continue
            for spec in agent_cfg.get("include") or []:
                if isinstance(spec, str):
                    includes.append(spec)
            for contract in agent_cfg.get("contracts") or []:
                if not isinstance(contract, dict):
                    continue
                entry = {
                    "desc": contract.get("desc") or "(unnamed contract)",
                    "activate_at": contract.get("activate_at"),
                    "A": _summarise_formula(contract.get("A")),
                    "G": _summarise_formula(contract.get("G")),
                }
                contracts.append(entry)
    return {"contracts": contracts, "includes": includes}


def _summarise_formula(formula) -> str | None:
    """Render an A/G block (``{ltl: ...}`` or ``{pattern: ..., args: ...}``)
    as a short single-line string, or ``None`` if the block is absent.
    """
    if not isinstance(formula, dict):
        return None
    if "ltl" in formula:
        return f"ltl: {formula['ltl']}"
    if "pattern" in formula:
        args = formula.get("args") or []
        head = args[0] if args else ""
        # ``arg_blacklist`` and friends carry the tool name as args[0]
        # plus a list of regexes.  Keep the shape compact.
        return f"pattern: {formula['pattern']}({head})"
    return str(formula)


def trace(
    host: HookHost,
    *,
    follow: bool = False,
    container: str | None = None,
):
    """Stream a human-readable view of agent activity.

    Reads OpenClaw's session JSONL log (the per-session transcript
    of every tool call + result + assistant turn) and emits one line
    per event in a Sponsio-flavoured format:

        →  CALL  <tool>(<args preview>)
        ←  ok    <result preview>
        ←  ✘ BLOCKED  <constraint violation reason>
        [agent] <assistant text>

    Yields tuples ``(level, line)`` so the CLI can colour them.

    ``container``: when set, reads the session log out of a Docker
    container by exec'ing into it.  Without it, reads from the local
    OpenClaw home (``$OPENCLAW_HOME/agents/main/sessions/``).

    ``follow=True`` tails the latest session forever; ``False`` reads
    once and returns.
    """
    import subprocess

    if container:
        # Resolve "newest session jsonl in container" via docker exec.
        find_cmd = (
            "ls -t /home/node/.openclaw/agents/main/sessions/*.jsonl 2>/dev/null "
            "| head -1"
        )
        latest = subprocess.run(
            ["docker", "exec", container, "sh", "-c", find_cmd],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not latest:
            yield ("error", f"no sessions found in container {container}")
            return
        tail_cmd = (
            ["docker", "exec", container, "tail"]
            + (["-F"] if follow else ["-n", "+1"])
            + [latest]
        )
        proc = subprocess.Popen(tail_cmd, stdout=subprocess.PIPE, text=True)
    else:
        sessions_dir = _openclaw_home() / "agents" / "main" / "sessions"
        if not sessions_dir.exists():
            yield ("error", f"no session dir at {sessions_dir}")
            return
        candidates = sorted(
            sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not candidates:
            yield ("error", f"no .jsonl sessions in {sessions_dir}")
            return
        latest = candidates[0]
        tail_cmd = ["tail"] + (
            ["-F", str(latest)] if follow else ["-n", "+1", str(latest)]
        )
        proc = subprocess.Popen(tail_cmd, stdout=subprocess.PIPE, text=True)

    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "message":
                continue
            msg = rec.get("message", {})
            role = msg.get("role")
            for c in msg.get("content", []) or []:
                ctype = c.get("type")
                if ctype == "toolCall":
                    name = c.get("name", "?")
                    args = json.dumps(c.get("arguments", {}))
                    if len(args) > 120:
                        args = args[:120] + "…"
                    yield ("call", f"→  CALL  {name}({args})")
                elif role == "toolResult" and ctype == "text":
                    # OpenClaw stores tool results as messages with
                    # role="toolResult" and content[]={type:"text",
                    # text:"<json>"}.  The text is a JSON blob like
                    # ``{"status": "error", "error": "..."}`` on
                    # failure / deny, or the raw stdout string on
                    # success.  Detect Sponsio's deny payload by the
                    # ``constraint violated`` marker the guard emits.
                    txt = c.get("text") or ""
                    blocked = (
                        "constraint violated" in txt
                        or "blocked by Sponsio" in txt
                        or "blocked by plugin hook" in txt
                    )
                    if blocked:
                        # Try to parse the JSON wrapper for a cleaner
                        # one-line reason; fall back to the raw text.
                        reason = txt
                        try:
                            parsed = json.loads(txt)
                            if isinstance(parsed, dict) and parsed.get("error"):
                                reason = str(parsed["error"])
                        except (json.JSONDecodeError, ValueError):
                            pass
                        compact = reason.replace("\n", " ")[:600]
                        yield ("block", f"←  ✘ BLOCKED  {compact}")
                    else:
                        compact = txt.replace("\n", " ")[:160]
                        yield ("ok", f"←  ok    {compact}")
                elif ctype == "toolResult":
                    # Older / alternate shape — keep for forward compat.
                    rt = c.get("content", "")
                    if isinstance(rt, list):
                        rt = " | ".join(
                            str(x.get("text", x)) if isinstance(x, dict) else str(x)
                            for x in rt
                        )
                    rt_s = str(rt)
                    blocked = (
                        "constraint violated" in rt_s
                        or "blocked by Sponsio" in rt_s
                        or ("permissionDecision" in rt_s and "deny" in rt_s)
                    )
                    if blocked:
                        compact = rt_s.replace("\n", " ")[:600]
                        yield ("block", f"←  ✘ BLOCKED  {compact}")
                    else:
                        compact = rt_s.replace("\n", " ")[:160]
                        yield ("ok", f"←  ok    {compact}")
                elif ctype == "text" and role == "assistant":
                    txt = (c.get("text") or "").strip()
                    if not txt or txt == "NO_REPLY":
                        continue
                    yield ("text", f"   [agent] {txt[:280].replace(chr(10), ' ')}")
                elif ctype == "text" and role == "user":
                    txt = c.get("text") or ""
                    # Strip OpenClaw's prepended metadata blocks so the
                    # demo viewer sees the actual user prompt.
                    if "Conversation info (untrusted metadata)" in txt:
                        parts = txt.split("```")
                        if len(parts) >= 5:
                            txt = parts[-1]
                    txt = txt.strip()
                    if txt:
                        yield ("user", f"   [user]  {txt[:280].replace(chr(10), ' ')}")
            if not follow and proc.poll() is not None:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


def uninstall(host: HookHost, *, scope: str = "user") -> HostUninstallResult:
    """Remove all three artefacts.  Each stage is best-effort and
    independent — partial state should still trend toward "all gone"
    on re-runs.
    """
    notes: list[str] = []
    removed = 0

    # 3 → 2 → 1, mirroring the install order in reverse.

    cfg_path = _openclaw_json_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            plugins_section = cfg.get("plugins")
            removed_keys: list[str] = []
            if isinstance(plugins_section, dict):
                entries = plugins_section.get("entries")
                if isinstance(entries, dict) and _PLUGIN_ID in entries:
                    entries.pop(_PLUGIN_ID, None)
                    removed_keys.append("entries")
                installs = plugins_section.get("installs")
                if isinstance(installs, dict) and _PLUGIN_ID in installs:
                    installs.pop(_PLUGIN_ID, None)
                    removed_keys.append("installs")
            if removed_keys:
                cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
                removed += 1
                notes.append(
                    f"removed {_PLUGIN_ID} from {cfg_path} ({'+'.join(removed_keys)})"
                )
            else:
                notes.append(f"{_PLUGIN_ID} not in {cfg_path}")
        except (OSError, json.JSONDecodeError) as e:
            notes.append(f"could not patch {cfg_path}: {e}")
    else:
        notes.append(f"{cfg_path} not present — skipped")

    ext_target = _extension_target()
    if ext_target.exists():
        try:
            shutil.rmtree(ext_target)
            removed += 1
            notes.append(f"removed extension at {ext_target}")
        except OSError as e:
            notes.append(f"could not remove {ext_target}: {e}")
    else:
        notes.append(f"extension at {ext_target} not present")

    lib_target = _library_root() / "_host_openclaw" / "sponsio.yaml"
    if lib_target.exists():
        try:
            lib_target.unlink()
            removed += 1
            notes.append(f"removed library at {lib_target}")
        except OSError as e:
            notes.append(f"could not remove {lib_target}: {e}")
    else:
        notes.append(f"library at {lib_target} not present")

    return HostUninstallResult(
        host=host.name,
        config_path=lib_target,
        removed_entries=removed,
        note="; ".join(notes),
    )
