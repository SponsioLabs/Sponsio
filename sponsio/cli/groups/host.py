"""``sponsio host`` — host-plugin lifecycle (install/status/trace/...)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import click

from sponsio.cli._shared import _contract_guarantee
from sponsio.cli.app import cli
from sponsio.cli.groups.plugin import _bootstrap_default_buckets, _install_one
from sponsio.cli.groups.skill import _packaged_skill_source


# ---------------------------------------------------------------------------
# Unified host integration. `sponsio host install/guard/list/uninstall`
#
# Wraps the per-host ``HookHost`` registry in :mod:`sponsio.integrations.hosts`
# behind one CLI surface.  Coexists with the legacy per-host commands
# (``sponsio cursor ...``, ``sponsio plugin guard ...``); the ``host``
# group is the recommended entry point going forward.
# ---------------------------------------------------------------------------


@cli.group()
def host():
    """Install, run, and inspect Sponsio host integrations.

    A *host* is an IDE or agent runtime Sponsio plugs into via shell
    hooks (Cursor, Claude Code, OpenClaw, …).  The framework-side
    onboarding (``sponsio onboard``) is for in-process wrap of agent
    code you own. separate axis, separate command.

    Subcommands:

    * ``sponsio host list``. show registered hosts and their install state.
    * ``sponsio host install <name>``. wire Sponsio into the host's hook
      config; ``auto`` / ``all`` install for every detected / known host.
    * ``sponsio host uninstall <name>``. remove Sponsio's entries, leave
      any user-authored hooks untouched.
    * ``sponsio host guard <name>``. runtime hook handler.  Called by
      the host's hook subprocess; users rarely invoke directly.
    """


@host.command(name="list")
def host_list():
    """Show registered hosts and which have configs on disk."""
    from sponsio.integrations import hosts as _hosts_mod

    # Force registration side-effects.
    _ = _hosts_mod.available()

    rows: list[tuple[str, str, str]] = []
    for h in _hosts_mod.available():
        user_path = h.config_path_user
        if user_path.exists():
            state = "✓ installed"
            path_str = str(user_path)
        elif any(p.exists() for p in h.detect_paths):
            state = "○ host present, sponsio not installed"
            path_str = str(user_path)
        else:
            state = "─ host not detected"
            path_str = str(user_path)
        rows.append((h.name, state, path_str))

    width_name = max(len(r[0]) for r in rows)
    width_state = max(len(r[1]) for r in rows)
    for name, state, path_str in rows:
        click.echo(f"  {name:<{width_name}}  {state:<{width_state}}  {path_str}")


@host.command(name="status")
@click.argument("name")
def host_status(name: str):
    """Show what Sponsio has deployed for ``<name>``.

    Hosts with a ``status_fn`` (currently OpenClaw) return a
    structured report of each install step + on-disk contract
    libraries.  Hosts without one fall back to a simple "is the
    config file there?" check.

    Use this when you want a single, scriptable answer to "is my
    Sponsio install for X actually in place". and to surface
    rule-library summaries for a recording or screenshot.
    """
    from sponsio.integrations import hosts as _hosts_mod

    try:
        host_spec = _hosts_mod.get(name)
    except KeyError as e:
        click.secho(f"✘  {e}", fg="red", err=True)
        sys.exit(1)

    if host_spec.status_fn is None:
        # Generic file-presence fallback so every registered host has
        # *some* status answer.
        installed = host_spec.config_path_user.exists()
        glyph = "✓" if installed else "○"
        colour = "green" if installed else "yellow"
        click.secho(
            f"{glyph}  {host_spec.name}: "
            f"{'config present' if installed else 'config missing'} "
            f"({host_spec.config_path_user})",
            fg=colour,
        )
        if not installed:
            sys.exit(1)
        return

    report = host_spec.status_fn(host_spec)
    click.secho(f"{host_spec.name}", fg="cyan", bold=True)

    any_failed = False
    for key in ("library", "extension", "registration"):
        entry = report.get(key)
        if not isinstance(entry, dict):
            continue
        ok = bool(entry.get("ok"))
        glyph = "✓" if ok else "✘"
        colour = "green" if ok else "red"
        click.secho(f"  {glyph}  {key}: {entry.get('detail', '')}", fg=colour)
        if not ok:
            any_failed = True

    libs = report.get("libraries")
    if isinstance(libs, list) and libs:
        click.secho("  ─  contract libraries:", fg="cyan")
        for lib in libs:
            name_ = lib.get("name", "?")
            contracts = lib.get("contracts") or []
            includes = lib.get("includes") or []
            err = lib.get("parse_error")
            header = f"     {name_}"
            if contracts:
                header += (
                    f"  ({len(contracts)} contract{'s' if len(contracts) != 1 else ''})"
                )
            click.secho(header, fg="cyan", bold=True)
            if err:
                click.secho(f"        (could not parse yaml: {err})", fg="yellow")
                continue
            for c in contracts:
                desc = c.get("desc") or "(unnamed)"
                tag = ""
                if c.get("activate_at"):
                    tag = f"  [activate_at: {c['activate_at']}]"
                click.echo(f"        • {desc}{tag}")
                a = c.get("A")
                g = _contract_guarantee(c)
                if a:
                    # 80-char window keeps the line readable on a
                    # demo terminal; full text lives in the YAML.
                    if len(a) > 96:
                        a = a[:96] + "…"
                    click.secho(f"            A:  {a}", fg="white", dim=True)
                if g:
                    if len(g) > 96:
                        g = g[:96] + "…"
                    click.secho(f"            G:  {g}", fg="white", dim=True)
            for inc in includes:
                click.secho(
                    f"        + bundled pack: {inc}",
                    fg="cyan",
                    dim=True,
                )

    if any_failed:
        sys.exit(1)


@host.command(name="trace")
@click.argument("name")
@click.option(
    "--follow/--no-follow",
    "-f",
    default=False,
    show_default=True,
    help="Tail the latest agent session forever.  Without it, prints once and exits.",
)
@click.option(
    "--container",
    "container",
    default=None,
    help=(
        "Read sessions from inside a Docker container instead of the local "
        "filesystem.  Convenient when the host runs as a container with "
        "``~/.openclaw`` *not* bind-mounted to a host path you can read."
    ),
)
def host_trace(name: str, follow: bool, container: str | None):
    """Stream agent activity (tool calls + Sponsio blocks) in real time.

    Useful as a side terminal during demos: the audience sees what
    the agent is doing and where Sponsio steps in.  Each line is
    coloured by event type:

    \b
    →  CALL   (yellow)  tool the agent invoked
    ←  ok    (green)   tool succeeded
    ←  ✘ BLOCKED (red) tool denied by Sponsio (deny reason inline)
    [agent] (blue)     assistant text
    [user]  (dim)      user text (Telegram metadata stripped)
    """
    from sponsio.integrations import hosts as _hosts_mod

    try:
        host_spec = _hosts_mod.get(name)
    except KeyError as e:
        click.secho(f"✘  {e}", fg="red", err=True)
        sys.exit(1)

    if host_spec.trace_fn is None:
        click.secho(
            f"✘  {host_spec.name}: no trace adapter for this host",
            fg="red",
            err=True,
        )
        sys.exit(1)

    from sponsio.render.host_trace import make_stdout_console, print_line

    console = make_stdout_console()
    try:
        for level, line in host_spec.trace_fn(
            host_spec, follow=follow, container=container
        ):
            print_line(console, level, line)
    except KeyboardInterrupt:
        # Clean exit on Ctrl-C so the recording terminal doesn't show a stack trace.
        click.echo()


def _resolve_host_targets(name_or_set: str) -> list[str]:
    """Map a CLI ``<name>`` token into a list of registered host ids.

    Supports ``auto`` (only hosts whose detect_paths match) and ``all``
    (every registered host).  Comma-separated lists also accepted:
    ``cursor,claude-code``.
    """
    from sponsio.integrations import hosts as _hosts_mod

    token = name_or_set.strip()
    if token == "all":
        return [h.name for h in _hosts_mod.available()]
    if token == "auto":
        detected = _hosts_mod.detect_installed()
        if not detected:
            return [h.name for h in _hosts_mod.available()]
        return [h.name for h in detected]
    if "," in token:
        return [t.strip() for t in token.split(",") if t.strip()]
    return [token]


# Per-host skill discovery roots, used by `sponsio host install --with-skill`.
# Each entry maps host name → (user-scope skill parent dir, project-scope skill
# parent dir | None).
#
# Cursor 2.4+, Claude Code, and Codex all consume the same Agent Skills open
# standard.  OpenClaw doesn't ship a documented skill discovery path today;
# we install to ``~/.openclaw/skills/`` by convention so the skill is
# materialised somewhere predictable, even if OpenClaw itself doesn't yet
# auto-discover it. the user (or a future OpenClaw release) can wire it in.
_HOST_SKILL_DIRS: dict[str, tuple[Path, Path | None]] = {
    "cursor": (
        Path.home() / ".cursor" / "skills",
        Path(".cursor") / "skills",
    ),
    "claude-code": (
        Path.home() / ".claude" / "skills",
        Path(".claude") / "skills",
    ),
    "openclaw": (
        Path.home() / ".openclaw" / "skills",
        Path(".openclaw") / "skills",
    ),
}


def _resolve_runtime_mode(explicit: str | None, *, allow_prompt: bool = True) -> str:
    """Pick the runtime mode for a fresh sponsio.yaml / host bucket.

    Single shared resolver for ``sponsio init`` / ``sponsio onboard`` /
    ``sponsio host install`` so all three present the same observe
    vs. enforce question to the user. Three sources, in precedence
    order:

    1. ``--mode`` flag on the command (skip the prompt).
    2. Interactive Y/N-style prompt. only if ``allow_prompt`` is true
       AND stdin is a tty (so CI / piped invocations don't hang).
    3. Default ``"observe"``. the safe shadow-mode first run.

    ``allow_prompt=False`` lets callers opt out of interactive mode
    even on a tty (for ``--json`` / ``--emit-context`` / ``--no-interactive``
    invocations where structured stdout must not be polluted by a
    prompt).
    """
    if explicit is not None:
        return explicit
    if not allow_prompt or not sys.stdin.isatty():
        return "observe"
    click.echo(
        "\nRuntime mode:\n"
        "  observe   shadow. checks run + log; tool behavior unchanged  (safe first run)\n"
        "  enforce   active. block / retry-with-feedback / escalate per violation type"
    )
    return click.prompt(
        "Mode",
        type=click.Choice(["observe", "enforce"]),
        default="observe",
        show_default=True,
    )


# Backward-compat alias. earlier code imported the more specific name.
_resolve_install_mode = _resolve_runtime_mode


def _apply_install_mode_to_host_buckets(
    host_name: str, mode: str
) -> list[tuple[Path, str]]:
    """Stamp ``defaults.mode: <mode>`` on freshly-bootstrapped buckets.

    Walks the per-host main + sub-agent buckets for ``host_name``, and
    for each one whose ``sponsio.yaml`` exists on disk:

    * If the file already has a ``defaults:`` block with ``mode:``,
      leave it alone. the user's choice (or a previous install)
      wins. This is the load-bearing "never overwrite" promise.
    * Otherwise, add a top-level ``defaults: { mode: <mode> }``
      block right after the ``version:`` line.

    Returns a list of ``(path, note)`` tuples suitable for the CLI
    to surface to the user (one per bucket touched). Never raises.
    a malformed yaml just gets reported and skipped.
    """
    import os as _os
    import re

    root_env = _os.environ.get("SPONSIO_PLUGIN_ROOT")
    root = (
        Path(root_env).expanduser()
        if root_env
        else Path.home() / ".sponsio" / "plugins"
    )
    main_bucket, sub_bucket = _bucket_for_host_name(host_name)
    candidates = [
        root / main_bucket / "sponsio.yaml",
        root / sub_bucket / "sponsio.yaml",
    ]

    out: list[tuple[Path, str]] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            out.append((path, f"could not read: {e}"))
            continue
        # Line-walking check (ReDoS-free).  Originally a single regex
        # ``^defaults:\s*$\n(?:[ \t]+.*\n)*[ \t]+mode:`` flagged by
        # CodeQL py/redos for nested-quantifier backtracking on inputs
        # with many ``\t\t\n`` lines; rewritten to explicit iteration
        # so there is no regex engine to backtrack.
        in_defaults = False
        already_has_mode = False
        for line in text.splitlines():
            if not in_defaults:
                if line.rstrip() == "defaults:":
                    in_defaults = True
                continue
            # Within the defaults: block; indented lines belong to it.
            if line and not line[0].isspace():
                break  # block ended without mode:
            if line.lstrip().startswith("mode:"):
                already_has_mode = True
                break
        if already_has_mode:
            out.append((path, "mode already set, kept"))
            continue
        # Insert ``defaults:\n  mode: <mode>\n`` after the version line.
        # If there's no ``version:`` line, prepend at top of file.
        defaults_block = f"defaults:\n  mode: {mode}  # observe|enforce. observe = shadow (safe default)\n\n"
        if re.search(r"^version:\s*", text, re.MULTILINE):
            new_text = re.sub(
                r"(^version:[^\n]*\n)",
                lambda m: m.group(1) + "\n" + defaults_block,
                text,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            new_text = defaults_block + text
        try:
            path.write_text(new_text, encoding="utf-8")
            out.append((path, f"set mode={mode}"))
        except OSError as e:
            out.append((path, f"could not write: {e}"))
    return out


def _refresh_per_host_bundles(
    host_name: str, plugin_root: Path
) -> list[tuple[str, str]]:
    """Install or smart-merge the ``_host_<host>`` + subagent bundles.

    Called from ``sponsio host install`` so a single command lays
    down the per-host contract libraries (in addition to the hook
    config and the ``_host`` legacy fallback). Returns a list of
    ``(message, colour)`` tuples for the caller to render. keeps
    this helper free of click side effects so it's testable.

    Idempotent and non-destructive. always safe to re-run:

    * Bundle missing → fresh install (writes the bundled starter,
      source-stamped so a later install can partition).
    * Bundle exists → ``_install_one`` smart merge (default contracts
      replaced from the new bundled YAML; user-authored contracts
      and the ``customized:`` block survive verbatim).
    * Bundle name not in the registry (e.g. host has no shipped
      starter for the subagent slot) → silently skipped.
    """
    from sponsio.plugin.registry import list_bundled

    bundled = set(list_bundled())
    main_bucket, sub_bucket = _bucket_for_host_name(host_name)
    out: list[tuple[str, str]] = []
    for bucket in (main_bucket, sub_bucket):
        if bucket not in bundled:
            continue
        target = plugin_root / bucket / "sponsio.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        kept = _install_one(bucket, target)
        if kept is None:
            out.append((f"✔  {host_name} bundle: wrote {target}", "green"))
        else:
            out.append(
                (
                    f"✔  {host_name} bundle: upgraded {target}. kept "
                    f"{kept['user_contracts']} customized contract(s) "
                    f"and {kept['customized']} customized entry/entries",
                    "green",
                )
            )
    return out


def _bucket_for_host_name(host_name: str) -> tuple[str, str]:
    """Bucket names baked into the per-host skill copy.

    The Skill is copied verbatim into each host's skill directory but
    its template placeholders for the ``_host_*`` library paths are
    rewritten at copy time so the agent under guard always writes
    contracts to the correct per-host bucket. We bake them in (rather
    than have the agent infer the host at runtime) because runtime
    detection is fragile. same Claude Code binary can show up under
    different host ids depending on how it was launched, and a wrong
    inference would write contracts to a bucket that no hook reads.

    Returns ``(main_bucket, subagent_bucket)``. OpenClaw doesn't have a
    subagent surface today; we still pick a name so the placeholder
    resolves cleanly even if the file is never created.
    """
    return (
        f"_host_{host_name.replace('-', '_')}",
        f"_host_{host_name.replace('-', '_')}_subagent",
    )


def _materialize_skill(src: Path, dst: Path, host_name: str) -> None:
    """Copy ``src`` to ``dst`` and substitute per-host bucket placeholders.

    Recursive (the skill ships as a directory). Files are read as text
    and written with placeholders resolved; binary files (if any are
    ever added) would need a separate bypass. none today.
    """
    main_bucket, sub_bucket = _bucket_for_host_name(host_name)
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.rglob("*"):
        relative = entry.relative_to(src)
        target = dst / relative
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        text = entry.read_text(encoding="utf-8")
        # Substitute the longer placeholder first so the prefix match
        # of {{HOST_BUCKET}} doesn't eat {{HOST_BUCKET_SUBAGENT}}.
        text = text.replace("{{HOST_BUCKET_SUBAGENT}}", sub_bucket)
        text = text.replace("{{HOST_BUCKET}}", main_bucket)
        target.write_text(text, encoding="utf-8")


def _install_skill_for_host(
    host_name: str, *, scope: str, force: bool
) -> tuple[bool, str]:
    """Copy the bundled Sponsio skill into the host's skill directory.

    Per-host bucket placeholders in the skill content
    (``{{HOST_BUCKET}}`` / ``{{HOST_BUCKET_SUBAGENT}}``) are
    substituted with this host's actual bucket names so the installed
    skill writes contracts straight to ``_host_<host>/sponsio.yaml``
    without runtime detection.

    Returns ``(written, note)``.  ``written=False`` is informational
    (already present, host has no skill standard, etc.). not a hard
    error.
    """
    if host_name not in _HOST_SKILL_DIRS:
        return False, f"{host_name}: no skill discovery path standard. skipped"

    user_parent, project_parent = _HOST_SKILL_DIRS[host_name]
    parent = project_parent if scope == "project" and project_parent else user_parent
    target = parent / "sponsio"

    src = _packaged_skill_source()

    parent.mkdir(parents=True, exist_ok=True)

    if target.exists() or target.is_symlink():
        if not force:
            return False, f"skill already at {target}. pass --force to replace"
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)

    _materialize_skill(src, target, host_name)
    return True, f"wrote skill to {target}"


def _uninstall_skill_for_host(host_name: str, *, scope: str) -> tuple[bool, str]:
    """Remove the bundled Sponsio skill from the host's skill directory.

    Symmetric to :func:`_install_skill_for_host` so ``sponsio host
    uninstall <host>`` reverts everything ``sponsio host install
    <host>`` planted (skill + extension + config patch + fallback
    library).  Without this, the skill silently lingered in
    ``~/.<host>/skills/sponsio/`` after uninstall, surprising users
    who expected the inverse of install.

    Returns ``(removed, note)``.  ``removed=False`` is informational
    (already gone, host has no skill standard, permission denied).
    not a hard error.
    """
    if host_name not in _HOST_SKILL_DIRS:
        return False, f"{host_name}: no skill discovery path standard. skipped"

    user_parent, project_parent = _HOST_SKILL_DIRS[host_name]
    parent = project_parent if scope == "project" and project_parent else user_parent
    target = parent / "sponsio"

    if not target.exists() and not target.is_symlink():
        return False, f"skill not present at {target}"

    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
    except OSError as e:
        return False, f"could not remove {target}: {e}"
    return True, f"removed skill from {target}"


@host.command(name="install")
@click.argument("names", nargs=-1, required=True)
@click.option(
    "--scope",
    type=click.Choice(["user", "project"]),
    default="user",
    show_default=True,
    help=(
        "``user`` writes to the host's user-level config "
        "(e.g. ``~/.cursor/hooks.json``).  ``project`` writes to a "
        "repo-local file (e.g. ``./.cursor/hooks.json``)."
    ),
)
@click.option(
    "--fail-closed/--fail-open",
    default=True,
    show_default=True,
    help=(
        "When the hook script itself fails, should the host block the "
        "tool call?  Default fail-closed prefers safety; ``--fail-open`` "
        "prefers availability.  Honoured by hosts that distinguish."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Overwrite the host's existing config (and skill if "
        "``--with-skill``).  Default merges Sponsio's entries in place "
        "for hooks; skill install is no-op when target exists."
    ),
)
@click.option(
    "--binary",
    "binary_override",
    type=str,
    default=None,
    help=(
        "Absolute path to the ``sponsio`` binary the hook should invoke.  "
        "Default is the binary backing the current process. always an "
        "absolute path, since hosts launch hook subprocesses from a "
        "minimal PATH that often misses venvs and ``~/.local/bin``."
    ),
)
@click.option(
    "--with-skill/--no-skill",
    default=True,
    show_default=True,
    help=(
        "Also copy the bundled Sponsio Agent Skill into the host's skill "
        "directory (Cursor 2.4+, Claude Code, Codex, OpenClaw via the "
        "linked chatbot). Skill teaches the agent to drive Sponsio's "
        "CLI for setup / scan / report; hook enforces contracts at the "
        "action boundary. Default ON. they're complementary, the "
        "without-skill flow is rare. Pass ``--no-skill`` to suppress."
    ),
)
@click.option(
    "--mode",
    type=click.Choice(["observe", "enforce"]),
    default=None,
    help=(
        "Initial runtime mode written into the bootstrapped per-host "
        "library (``defaults.mode``). ``observe`` (recommended) shadow-"
        "logs every violation without blocking; ``enforce`` blocks at "
        "the action boundary. Skip the flag to be prompted "
        "interactively. Doesn't overwrite a mode already set in an "
        "existing on-disk library."
    ),
)
def host_install(
    names: tuple[str, ...],
    scope: str,
    fail_closed: bool,
    force: bool,
    binary_override: str | None,
    with_skill: bool,
    mode: str | None,
):
    """Install Sponsio as a hook handler for one or more hosts.

    Bootstraps the default contract library (``~/.sponsio/plugins/_host``
    and friends) on the way in, so a single invocation gives you a
    fully-wired hook + the rules it reads. no separate
    ``sponsio plugin init`` step required.

    \b
    Examples:
      sponsio host install cursor
      sponsio host install cursor claude-code
      sponsio host install all
      sponsio host install auto              # only hosts detected on this machine
      sponsio host install cursor --scope project
    """
    from sponsio.integrations import hosts as _hosts_mod

    targets: list[str] = []
    for token in names:
        targets.extend(_resolve_host_targets(token))
    # Dedup while preserving order.
    seen: set[str] = set()
    targets = [t for t in targets if not (t in seen or seen.add(t))]

    # Resolve runtime mode once for all hosts in this invocation. The
    # prompt mirrors ``sponsio init``'s mode prompt so first-time users
    # see the same observe-vs-enforce question regardless of entry
    # point. ``observe`` is the default if non-interactive (CI, piped
    # stdin). same precedent as init_wizard.
    chosen_mode = _resolve_install_mode(mode)
    click.echo(f"Runtime mode for new host libraries: {chosen_mode}")

    # Bootstrap the default contract library buckets (``_host`` etc.)
    # the hook will read at runtime. folded in here so users don't
    # have to remember a separate ``sponsio plugin init`` step. Silent
    # if everything already exists; reports any fresh writes.
    plugin_root_env = os.environ.get("SPONSIO_PLUGIN_ROOT")
    plugin_root = (
        Path(plugin_root_env).expanduser()
        if plugin_root_env
        else Path.home() / ".sponsio" / "plugins"
    )
    for path, status in _bootstrap_default_buckets(plugin_root):
        if status == "wrote":
            click.secho(f"✔  bootstrapped contract library: {path}", fg="green")
        elif status.startswith("error:"):
            click.secho(
                f"✘  could not bootstrap {path.parent.name!r}: "
                f"{status[len('error:') :].strip()}. reinstall sponsio.",
                fg="red",
                err=True,
            )

    # Detect the legacy ``_host/sponsio.yaml`` and suggest migration.
    # Without this, the user installs ``_host_<name>`` thinking they
    # have a single source of truth but the runtime still falls back
    # to ``_host`` when the per-host yaml is missing. the dual-yaml
    # confusion this whole migration story exists to retire.
    legacy_host_yaml = plugin_root / "_host" / "sponsio.yaml"
    if legacy_host_yaml.exists():
        migratable = [t for t in targets if t in _LEGACY_HOST_NAME_TO_BUCKET]
        if migratable:
            click.secho(
                "⚠  legacy `_host/sponsio.yaml` detected. runtime will "
                "still fall back to it when per-host buckets are missing.\n"
                f"   Consolidate with:  sponsio host migrate "
                f"{' '.join(migratable)}",
                fg="yellow",
            )

    any_failed = False
    review_paths: list[Path] = []
    for name in targets:
        try:
            host_spec = _hosts_mod.get(name)
        except KeyError as e:
            click.secho(f"✘  {e}", fg="red", err=True)
            any_failed = True
            continue
        result = host_spec.install_fn(
            host_spec,
            scope=scope,
            fail_closed=fail_closed,
            force=force,
            binary=binary_override,
        )
        glyph = "✔" if result.written else "○"
        colour = "green" if result.written else "yellow"
        click.secho(
            f"{glyph}  {result.host}: {result.note}",
            fg=colour,
        )
        click.echo(f"     {result.config_path}")
        if not result.written:
            # Existing-but-not-overwritten is informational, not a failure.
            pass

        # Lay down (or refresh) the per-host contract bundles
        # ``_host_<name>`` / ``_host_<name>_subagent``. Without this
        # step a fresh ``host install cursor`` would only write the
        # hook config + the legacy ``_host`` fallback library, so
        # Cursor would run on Claude-Code-shaped rules instead of its
        # own. ``_install_one`` is idempotent: missing bundle → fresh
        # write; existing bundle → smart-merge upgrade (default
        # contracts replaced from the new bundled YAML; user-authored
        # contracts and the ``customized:`` block survive verbatim).
        bundle_summary = _refresh_per_host_bundles(name, plugin_root)
        for line, colour in bundle_summary:
            click.secho(line, fg=colour)

        # Stamp the chosen mode onto the freshly-bootstrapped per-host
        # library, but never clobber a mode the user has already set.
        # Done after install so the bucket directory exists.
        applied = _apply_install_mode_to_host_buckets(name, chosen_mode)
        for path, note in applied:
            click.secho(f"○  {name} mode: {note}", fg="yellow")
            click.echo(f"     {path}")
            review_paths.append(path)

        if with_skill:
            written, note = _install_skill_for_host(name, scope=scope, force=force)
            glyph = "✔" if written else "○"
            colour = "green" if written else "yellow"
            click.secho(f"{glyph}  {name} skill: {note}", fg=colour)

    # Final review pointer. surface the bootstrapped per-host
    # library paths so the user immediately knows where to look /
    # what to read before flipping to enforce. The bundled starter
    # ships sane defaults, but the privileged-action surface
    # (Bash blacklist, secret-shape rules, rate_limit thresholds)
    # is something the operator should still see with their own eyes.
    if review_paths:
        click.echo()
        click.secho("Review the bootstrapped contract libraries:", bold=True)
        for path in review_paths:
            click.echo(f"  {click.style(str(path), fg='green')}")
        click.secho(
            "  (open each, sanity-check the rules, then re-run with `--mode enforce`",
            dim=True,
        )
        click.secho("   when you're ready to switch from observe to active)", dim=True)

    if any_failed:
        sys.exit(1)


_LEGACY_HOST_NAME_TO_BUCKET: dict[str, str] = {
    "claude-code": "_host_claude_code",
    "cursor": "_host_cursor",
}


@host.command(name="migrate")
@click.argument("names", nargs=-1, required=True)
@click.option(
    "--keep-legacy",
    is_flag=True,
    help=(
        "Don't delete the legacy `_host/sponsio.yaml` after migrating.  "
        "Default is to delete. having both files around is the source "
        "of the dual-yaml confusion this command fixes."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Overwrite an existing `_host_<name>/sponsio.yaml`.  Default "
        "is to refuse if the per-host bucket is already populated, so "
        "we don't silently clobber user customisations."
    ),
)
def host_migrate(names: tuple[str, ...], keep_legacy: bool, force: bool):
    """Migrate the legacy `_host` bucket to per-host buckets.

    Until 0.1.x, ``sponsio plugin init`` wrote a single
    ``~/.sponsio/plugins/_host/sponsio.yaml`` that gated every Claude
    Code AND Cursor invocation.  Per-host routing
    (``_host_claude_code/`` / ``_host_cursor/``) supersedes that.
    Existing installs keep working through a runtime fallback, but
    that fallback is the source of "I deleted _host_claude_code,
    why is it still blocking?". the legacy bucket silently kicks
    in.

    This command consolidates: it copies
    ``~/.sponsio/plugins/_host/sponsio.yaml`` into one or more
    ``~/.sponsio/plugins/_host_<name>/sponsio.yaml`` files (rewriting
    the ``agents:`` key on the way), then deletes the legacy file.

    Pass ``auto`` to migrate every host that ``sponsio host
    list`` reports as installed.

    \b
    Examples:
      sponsio host migrate claude-code
      sponsio host migrate claude-code cursor
      sponsio host migrate auto                 # every detected host
    """
    plugin_root_env = os.environ.get("SPONSIO_PLUGIN_ROOT")
    plugin_root = (
        Path(plugin_root_env).expanduser()
        if plugin_root_env
        else Path.home() / ".sponsio" / "plugins"
    )
    legacy_path = plugin_root / "_host" / "sponsio.yaml"

    if not legacy_path.exists():
        click.secho(
            f"✘ legacy bucket not found at {legacy_path}. nothing to migrate.",
            fg="yellow",
            err=True,
        )
        sys.exit(1)

    legacy_text = legacy_path.read_text(encoding="utf-8")

    # Expand ``auto`` to every host with a per-host bucket OR a
    # detected binary on PATH.
    targets: list[str] = []
    for token in names:
        if token == "auto":
            for host_name in _LEGACY_HOST_NAME_TO_BUCKET:
                if shutil.which(host_name.split("-")[0]):
                    targets.append(host_name)
        else:
            targets.append(token)
    seen: set[str] = set()
    targets = [t for t in targets if not (t in seen or seen.add(t))]

    valid = [t for t in targets if t in _LEGACY_HOST_NAME_TO_BUCKET]
    invalid = [t for t in targets if t not in _LEGACY_HOST_NAME_TO_BUCKET]
    if invalid:
        click.secho(
            f"✘ unknown host(s): {', '.join(invalid)}.  "
            f"Supported: {', '.join(_LEGACY_HOST_NAME_TO_BUCKET)}",
            fg="red",
            err=True,
        )
        sys.exit(1)
    if not valid:
        click.secho(
            "✘ no hosts to migrate (auto found nothing).  "
            "Specify host name(s) explicitly.",
            fg="yellow",
            err=True,
        )
        sys.exit(1)

    written: list[Path] = []
    for host_name in valid:
        bucket = _LEGACY_HOST_NAME_TO_BUCKET[host_name]
        target_path = plugin_root / bucket / "sponsio.yaml"
        if target_path.exists() and not force:
            click.secho(
                f"✘ {target_path} already exists. pass --force to overwrite.",
                fg="red",
                err=True,
            )
            sys.exit(1)
        # Rewrite the agents key on the way: the legacy file has
        # ``agents: _host:`` (or ``_host_subagent:``); the per-host
        # bucket needs ``agents: <bucket>:``.  Plain string replace
        # is safe here. those exact lines have no other meaning in
        # a contract yaml.
        new_text = legacy_text.replace(
            "agents:\n  _host:", f"agents:\n  {bucket}:"
        ).replace("\n  _host:\n", f"\n  {bucket}:\n")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_text, encoding="utf-8")
        written.append(target_path)
        click.secho(f"✔  wrote {target_path}", fg="green")

    if not keep_legacy:
        legacy_path.unlink()
        click.secho(f"✔  removed {legacy_path}", fg="green")
    else:
        click.secho(
            f"⚠  kept {legacy_path} (--keep-legacy). runtime will still "
            "fall back to it for hosts without a per-host bucket",
            fg="yellow",
        )

    click.echo()
    click.secho("Next steps:", bold=True)
    click.echo("  sponsio host status <name>      # confirm the migration")
    click.echo("  sponsio doctor                  # verify everything wires up")


@host.command(name="uninstall")
@click.argument("names", nargs=-1, required=True)
@click.option(
    "--scope",
    type=click.Choice(["user", "project"]),
    default="user",
    show_default=True,
)
@click.option(
    "--with-skill/--keep-skill",
    default=True,
    show_default=True,
    help=(
        "Also remove the bundled Sponsio Agent Skill from the host's "
        "skill directory.  Symmetric to ``host install --with-skill`` "
        "(also default-on).  Pass ``--keep-skill`` to leave the skill "
        "in place. useful when you're re-installing immediately and "
        "want to avoid an OpenClaw skill-cache bounce, or when the "
        "skill predates Sponsio at this host."
    ),
)
def host_uninstall(names: tuple[str, ...], scope: str, with_skill: bool):
    """Remove Sponsio's entries from one or more host configs.

    Leaves any non-Sponsio hooks untouched.  Use ``all`` to clean
    every registered host.

    Removes the bundled Sponsio skill by default (symmetric to
    ``host install``); pass ``--keep-skill`` to leave it.
    """
    from sponsio.integrations import hosts as _hosts_mod

    targets: list[str] = []
    for token in names:
        targets.extend(_resolve_host_targets(token))
    seen: set[str] = set()
    targets = [t for t in targets if not (t in seen or seen.add(t))]

    any_failed = False
    for name in targets:
        try:
            host_spec = _hosts_mod.get(name)
        except KeyError as e:
            click.secho(f"✘  {e}", fg="red", err=True)
            any_failed = True
            continue
        result = host_spec.uninstall_fn(host_spec, scope=scope)
        click.secho(f"○  {result.host}: {result.note}", fg="yellow")
        click.echo(f"     {result.config_path}")

        if with_skill:
            removed, note = _uninstall_skill_for_host(name, scope=scope)
            glyph = "✔" if removed else "○"
            colour = "green" if removed else "yellow"
            click.secho(f"{glyph}  {name} skill: {note}", fg=colour)
    if any_failed:
        sys.exit(1)


@host.command(name="guard")
@click.argument("name")
@click.option(
    "--event",
    "hook_event",
    type=str,
    default=None,
    help=(
        "For hosts with a multi-event protocol (Cursor: ``preToolUse``, "
        "``beforeShellExecution``, …), the event being handled.  Hosts "
        "with a single-event protocol (Claude Code, OpenClaw) ignore "
        "this. the event name lives in the JSON body."
    ),
)
@click.option(
    "--stdin",
    "use_stdin",
    is_flag=True,
    default=True,
    help="(default) Read one hook event as JSON from stdin.",
)
def host_guard(name: str, hook_event: str | None, use_stdin: bool):
    """Runtime hook handler. called by the host's hook subprocess.

    Reads a JSON payload from stdin, evaluates it against the matching
    Sponsio contract library, and writes the host-shaped reply.  Exits
    cleanly on internal errors so a Sponsio bug never wedges a real
    tool call.
    """
    from sponsio.integrations import hosts as _hosts_mod

    try:
        host_spec = _hosts_mod.get(name)
    except KeyError as e:
        sys.stderr.write(f"sponsio host guard: {e}\n")
        sys.exit(0)

    code = host_spec.runtime_fn(host_spec, hook_event, None)
    sys.exit(code)
