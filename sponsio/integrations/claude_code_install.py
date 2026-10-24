"""Claude Code install/uninstall — write hook entries into Claude
Code's user-level ``~/.claude/settings.json`` (or
``./.claude/settings.json`` for project scope).

Surfaced behind ``sponsio host install claude-code``.

Hook payload schema (Claude Code):

    {
      "PreToolUse": [
        { "matcher": "*",
          "hooks": [{ "type": "command",
                      "command": "<python> -I -S <shim> claude-code --socket ...",
                      "timeout": 10, "onFailure": "block" }] }
      ]
    }

The command is the shim (:func:`sponsio.integrations.hosts.shim_command`),
which forwards the event to the daemon and denies when it cannot. Two
host-side settings back it up on ``PreToolUse``: ``timeout`` is well
above the shim's own deadline, and ``onFailure: "block"`` makes a hook
that could not run (a missing interpreter, a timeout) block the call
instead of letting it through. Claude Code honours ``onFailure`` from
v2.1.295; older releases ignore it, and the shim's exit 2 with a deny
body is then the only signal. ``PostToolUse`` cannot block, so it
carries the timeout alone.

Reference: https://code.claude.com/docs/en/hooks
"""

from __future__ import annotations

import json
from pathlib import Path

from sponsio.integrations import config_backup
from sponsio.integrations.hosts import (
    CLAUDE_CODE_HOOK_TIMEOUT_S,
    HookHost,
    HostInstallResult,
    HostUninstallResult,
    _default_socket_path,
    is_sponsio_hook_command,
    set_daemon_mode,
    shim_command,
    socket_path_problem,
)

_INSTALLED_EVENTS = ("PreToolUse", "PostToolUse")


def _hook_entry(event: str, command: str) -> dict:
    """One ``hooks[]`` element for ``event``."""
    hook: dict = {
        "type": "command",
        "command": command,
        "timeout": CLAUDE_CODE_HOOK_TIMEOUT_S,
    }
    if event == "PreToolUse":
        hook["onFailure"] = "block"
    return {"matcher": "*", "hooks": [hook]}


def _is_sponsio_entry(prior: dict) -> bool:
    inner = prior.get("hooks") if isinstance(prior.get("hooks"), list) else []
    return any(
        isinstance(h, dict)
        and isinstance(h.get("command"), str)
        and is_sponsio_hook_command(h["command"], "claude-code")
        for h in inner
    )


def _ensure_bundled_library(bucket: str) -> None:
    """Bootstrap ``~/.sponsio/plugins/<bucket>/sponsio.yaml`` if absent.

    Each host now owns its own bucket under ``~/.sponsio/plugins/`` so
    per-IDE rules can diverge from the legacy shared ``_host`` library.
    The bundled starter is dropped in on first install only — subsequent
    installs leave the user's edits alone."""
    import os as _os

    from sponsio.plugin.registry import read_bundled

    root_env = _os.environ.get("SPONSIO_PLUGIN_ROOT")
    root = (
        Path(root_env).expanduser()
        if root_env
        else Path.home() / ".sponsio" / "plugins"
    )
    target = root / bucket / "sponsio.yaml"
    if target.exists():
        return
    try:
        text = read_bundled(bucket)
    except (FileNotFoundError, ModuleNotFoundError):
        return
    # Stamped like ``sponsio host install``'s own refresh writes it, so a
    # later upgrade tells the shipped contracts from the user's.
    from sponsio.cli.groups.plugin import _stamp_bundled_source

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_stamp_bundled_source(text, bucket), encoding="utf-8")


def _ensure_subagent_library() -> None:
    """Backward-compat shim — bootstraps the claude-code host buckets.

    Pre-host-bucketing this only seeded ``_host_subagent``. Now we seed
    both the main and sub-agent buckets for Claude Code; the old name
    is preserved in case any caller imported it directly.
    """
    _ensure_bundled_library("_host_claude_code")
    _ensure_bundled_library("_host_claude_code_subagent")


def install(
    host: HookHost,
    *,
    scope: str = "user",
    fail_closed: bool = True,
    force: bool = False,
    binary: str | None = None,
    mode: str = "enforce",
) -> HostInstallResult:
    """Write or merge Sponsio entries into Claude Code's settings.json.

    The command is the shim (see
    :func:`sponsio.integrations.hosts.shim_command`). ``mode`` goes into
    the daemon's config for this host (:func:`set_daemon_mode`), not on
    the command; ``observe`` makes the daemon log without blocking. ``binary``
    is what the shim starts a daemon with when none answers (default:
    this interpreter and ``-m sponsio.cli``).

    ``fail_closed`` is advisory: the entries always carry ``onFailure:
    "block"`` on ``PreToolUse``, and the shim always exits 2 with the
    deny body when it cannot reach the daemon.

    Side effect: bootstraps ``_host_subagent`` library if absent, so
    Task-spawned subagent calls have a stricter library to route to.
    """
    _ensure_subagent_library()
    if scope == "project":
        target = Path.cwd() / ".claude" / "settings.json"
    else:
        target = host.config_path_user

    cmd = shim_command("claude-code", sponsio=binary)

    problem = socket_path_problem(_default_socket_path())
    if problem:
        return HostInstallResult(
            host=host.name,
            config_path=target,
            written=False,
            note=(
                f"refusing to install: {problem}. A hook that cannot reach "
                "the daemon denies every tool call. Set SPONSIO_DAEMON_SOCKET "
                "to a shorter path and re-run"
            ),
        )

    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not force:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            return HostInstallResult(
                host=host.name,
                config_path=target,
                written=False,
                note=(
                    f"{target} exists but is not valid JSON — refusing "
                    "to merge.  Re-run with force=True to overwrite."
                ),
            )
        merged = dict(existing)
        hooks_root = (
            merged.get("hooks") if isinstance(merged.get("hooks"), dict) else {}
        )
        for event in _INSTALLED_EVENTS:
            entries = (
                hooks_root.get(event) if isinstance(hooks_root.get(event), list) else []
            )
            keep: list[dict] = []
            for prior in entries:
                if not isinstance(prior, dict):
                    keep.append(prior)
                    continue
                # Replace Sponsio's own entries, of any generation.
                if _is_sponsio_entry(prior):
                    continue
                keep.append(prior)
            keep.append(_hook_entry(event, cmd))
            hooks_root[event] = keep
        merged["hooks"] = hooks_root
        out = merged
        note = "merged into existing settings.json"
    else:
        out = {
            "hooks": {event: [_hook_entry(event, cmd)] for event in _INSTALLED_EVENTS}
        }
        note = "wrote fresh settings.json"

    saved = config_backup.write(target, json.dumps(out, indent=2) + "\n")
    note = f"{note}; mode {mode} {set_daemon_mode('claude-code', mode)}"
    return HostInstallResult(
        host=host.name,
        config_path=target,
        written=True,
        note=note,
        backup=saved,
    )


def uninstall(host: HookHost, *, scope: str = "user") -> HostUninstallResult:
    if scope == "project":
        target = Path.cwd() / ".claude" / "settings.json"
    else:
        target = host.config_path_user

    if not target.exists():
        return HostUninstallResult(
            host=host.name,
            config_path=target,
            removed_entries=0,
            note="no settings.json found — nothing to uninstall",
        )

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return HostUninstallResult(
            host=host.name,
            config_path=target,
            removed_entries=0,
            note="settings.json is malformed — refusing to edit",
        )

    hooks_root = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    removed = 0
    for event in _INSTALLED_EVENTS:
        entries = (
            hooks_root.get(event) if isinstance(hooks_root.get(event), list) else []
        )
        kept: list[dict] = []
        for prior in entries:
            if not isinstance(prior, dict):
                kept.append(prior)
                continue
            if _is_sponsio_entry(prior):
                removed += 1
                continue
            kept.append(prior)
        if kept:
            hooks_root[event] = kept
        elif event in hooks_root:
            del hooks_root[event]

    if hooks_root:
        data["hooks"] = hooks_root
    else:
        data.pop("hooks", None)

    if not removed:
        saved = None
        note = "no sponsio entries in settings.json"
    elif data:
        saved = config_backup.write(target, json.dumps(data, indent=2) + "\n")
        note = f"removed {removed} sponsio entries from settings.json"
    else:
        saved = config_backup.remove(target)
        note = f"removed {removed} sponsio entries; deleted now-empty settings.json"

    return HostUninstallResult(
        host=host.name,
        config_path=target,
        removed_entries=removed,
        note=note,
        backup=saved,
    )
