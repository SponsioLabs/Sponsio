"""``sponsio plugin`` — per-plugin contract library + host-plugin runtime."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path

import click

from sponsio.cli._shared import _contract_guarantee
from sponsio.cli.app import cli


# ---------------------------------------------------------------------------
# `sponsio plugin ...`. host-plugin runtime adapter
# ---------------------------------------------------------------------------
#
# The ``plugin`` subgroup hosts everything related to running Sponsio as a
# host-installed runtime over a plugin system (Claude Code, OpenClaw, …).
# ``plugin guard`` is the per-call hook entry; ``plugin init``, ``plugin
# install``, ``plugin scan``, ``plugin report``, and ``plugin status``
# (Stage-2/3) live behind the same group so users only have to learn one
# prefix.


@cli.group()
def plugin():
    """Host-plugin runtime for Claude Code, OpenClaw, …."""


def _bootstrap_default_buckets(
    root: Path, *, force: bool = False
) -> list[tuple[Path, str]]:
    """Write the ``_host`` / ``_host_subagent`` / ``_host_openclaw`` defaults.

    Shared by ``plugin init`` (explicit) and ``host install`` (implicit, so
    a single command wires the hook *and* lays down the contract library
    the hook reads). Silent. returns ``[(path, status), ...]`` where
    status is ``"wrote"`` (fresh write), ``"exists"`` (kept existing),
    or ``"error:<reason>"`` (bundled source missing). Callers decide how
    to render.
    """
    from sponsio.plugin.registry import read_bundled

    results: list[tuple[Path, str]] = []
    for lib_name in ("_host", "_host_subagent", "_host_openclaw"):
        target_dir = root / lib_name
        target = target_dir / "sponsio.yaml"
        try:
            src_text = read_bundled(lib_name)
        except (FileNotFoundError, ModuleNotFoundError) as e:
            results.append((target, f"error:{e}"))
            continue
        if target.exists() and not force:
            results.append((target, "exists"))
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(src_text, encoding="utf-8")
        results.append((target, "wrote"))
    return results


@plugin.command(name="init")
@click.option(
    "--root",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Override the per-plugin library root "
        "(default: $SPONSIO_PLUGIN_ROOT or ~/.sponsio/plugins)."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing _host/sponsio.yaml without prompting.",
)
@click.option(
    "--no-smoke-test",
    is_flag=True,
    default=False,
    help="Skip the post-install JSON-on-stdin verification.",
)
def plugin_init(root: Path | None, force: bool, no_smoke_test: bool):
    """Bootstrap ``~/.sponsio/plugins/`` with the default ``_host`` library.

    What this writes:

    \b
      <root>/_host/sponsio.yaml         from sponsio/plugin/defaults/_host.yaml

    The default ``_host`` library reuses ``sponsio:capability/shell`` to
    block ``rm -rf /``, fork bombs, ``curl|bash``, reverse-shell
    primitives, line-continuation evasion, and CVE-2026-28460-class
    escapes against Claude Code's first-party Bash tool.

    After running this, install or update the sponsio-claude-code plugin
    and load it with::

        claude --plugin-dir <path-to-sponsio-claude-code>

    Per-plugin libraries for individual MCP servers / plugins live as
    siblings of ``_host/`` and can be created by hand or via
    ``sponsio plugin scan``.
    """
    click.secho(
        "⚠  `sponsio plugin init` is deprecated. it writes the legacy "
        "`_host/` bucket that per-host routing now supersedes.\n"
        "   For new installs, use `sponsio host install <name>` "
        "instead (claude-code / cursor / openclaw).\n"
        "   To consolidate an existing `_host/` into per-host buckets, "
        "use `sponsio host migrate <name>`.",
        fg="yellow",
        err=True,
    )

    if root is None:
        env = os.environ.get("SPONSIO_PLUGIN_ROOT")
        root = Path(env).expanduser() if env else Path.home() / ".sponsio" / "plugins"

    results = _bootstrap_default_buckets(root, force=force)
    for path, status in results:
        if status == "wrote":
            click.secho(f"✓ wrote {path}", fg="green")
        elif status == "exists":
            click.echo(f"{path} already exists. Re-run with --force to overwrite.")
        elif status.startswith("error:"):
            click.secho(
                f"Error: bundled default library missing for {path.parent.name!r} "
                f"({status[len('error:') :].strip()}). Reinstall sponsio.",
                fg="red",
            )
            sys.exit(1)

    # Smoke test runs against ``_host`` (the Claude-Code-shape fallback).
    # the test prompt is a Bash ``rm -rf /`` which needs that library.
    # When no fresh ``_host`` write happened (existing file kept), skip
    # rather than validating someone's customised library.
    wrote_file = any(
        path.parent.name == "_host" and status == "wrote" for path, status in results
    )

    # Smoke test: feed a JSON event through the actual hook entry point
    # and verify it (a) allows a benign command and (b) blocks rm -rf.
    # Skip when we kept an existing user file. their library may diverge
    # from the default in legitimate ways and we shouldn't fail-closed
    # on its content.
    if no_smoke_test or not wrote_file:
        if not wrote_file:
            click.echo("Skipped smoke test (existing file kept).")
        else:
            click.echo("Skipped smoke test (--no-smoke-test).")
        _print_plugin_next_steps()
        return

    from sponsio.guard_stdin import run_stdin

    saved_root = os.environ.get("SPONSIO_PLUGIN_ROOT")
    os.environ["SPONSIO_PLUGIN_ROOT"] = str(root)
    try:
        # (a) allow a benign Bash command
        captured_out = io.StringIO()
        with contextlib.redirect_stdout(captured_out):
            allow_code = run_stdin(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": "echo hello"},
                    }
                )
            )
        allow_ok = allow_code == 0 and captured_out.getvalue().strip() == ""

        # (b) block rm -rf /
        captured_out = io.StringIO()
        with contextlib.redirect_stdout(captured_out):
            block_code = run_stdin(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": "rm -rf /"},
                    }
                )
            )
        block_payload = captured_out.getvalue().strip()
        block_ok = block_code == 0 and block_payload and '"deny"' in block_payload
    finally:
        if saved_root is None:
            os.environ.pop("SPONSIO_PLUGIN_ROOT", None)
        else:
            os.environ["SPONSIO_PLUGIN_ROOT"] = saved_root

    if allow_ok and block_ok:
        click.secho("✓ smoke test: allow + block both work", fg="green")
    else:
        click.secho(
            f"✗ smoke test failed (allow_ok={allow_ok}, block_ok={block_ok}). "
            f"Library may be malformed or sponsio CLI is mis-installed.",
            fg="red",
        )
        sys.exit(1)

    _print_plugin_next_steps()


def _print_plugin_next_steps() -> None:
    """User-facing pointer to the next manual step."""
    click.echo("")
    click.echo("Next:")
    click.echo("  1. Clone or download the sponsio-claude-code plugin.")
    click.echo("  2. Load it in Claude Code:")
    click.echo("       claude --plugin-dir /path/to/sponsio-claude-code")
    click.echo("  3. Issue any Bash tool call. the plugin wraps it.")
    click.echo("")
    click.echo("Add starter libraries for popular MCP servers:")
    click.echo("  sponsio plugin install --list   # see what's bundled")
    click.echo("  sponsio plugin install github   # copy github starter")


@plugin.command(name="install")
@click.argument("names", nargs=-1)
@click.option(
    "--list",
    "list_only",
    is_flag=True,
    default=False,
    help="List bundled starter libraries and exit.",
)
@click.option(
    "--all",
    "install_all",
    is_flag=True,
    default=False,
    help="Install every bundled library (skips ``_host``. use ``init`` for that).",
)
@click.option(
    "--root",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Override the per-plugin library root "
        "(default: $SPONSIO_PLUGIN_ROOT or ~/.sponsio/plugins)."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Accepted for back-compat; no-op. ``install`` is always "
        "idempotent. fresh install or smart-merge upgrade, never "
        "destructive."
    ),
)
def plugin_install(
    names: tuple[str, ...],
    list_only: bool,
    install_all: bool,
    root: Path | None,
    force: bool,
):
    """Copy bundled starter libraries into ``~/.sponsio/plugins/<name>/``.

    Each starter is a hand-curated contract library for a popular
    plugin / MCP server (github, filesystem, playwright, …). Run
    ``--list`` to see what's bundled with the current sponsio install.

    Examples:

    \b
        sponsio plugin install --list
        sponsio plugin install github
        sponsio plugin install github filesystem playwright
        sponsio plugin install --all
    """
    from sponsio.plugin.registry import list_bundled

    bundled = list_bundled()

    if list_only:
        click.echo("Bundled starter libraries:")
        for n in bundled:
            marker = " (auto-installed by `plugin init`)" if n == "_host" else ""
            click.echo(f"  {n}{marker}")
        return

    if install_all:
        # Fallback host libraries (``_host`` for Claude Code,
        # ``_host_openclaw`` for OpenClaw) are owned by ``plugin init``
        # and have their own smoke-test path; don't double-write here.
        names = tuple(
            n for n in bundled if n not in {"_host", "_host_subagent", "_host_openclaw"}
        )

    if not names:
        click.secho(
            "Error: pass at least one library name, or --all / --list.\n"
            f"Bundled: {', '.join(bundled)}",
            fg="red",
        )
        sys.exit(2)

    unknown = [n for n in names if n not in bundled]
    if unknown:
        click.secho(
            f"Error: unknown bundled libraries {unknown}. "
            f"Available: {', '.join(bundled)}.",
            fg="red",
        )
        sys.exit(2)

    if root is None:
        env = os.environ.get("SPONSIO_PLUGIN_ROOT")
        root = Path(env).expanduser() if env else Path.home() / ".sponsio" / "plugins"

    # ``install`` is always idempotent and non-destructive:
    #
    # * Library missing → fresh write of the bundled starter (source-
    #   stamped so a later install can partition).
    # * Library exists → ``_install_one`` smart merge (default
    #   contracts replaced from the new bundled YAML; user-authored
    #   contracts and the ``customized:`` block survive verbatim).
    #
    # ``--force`` used to gate the upgrade path; it's now a silent
    # no-op kept for back-compat with existing scripts.
    written: list[Path] = []
    skipped: list[Path] = []  # noqa: F841 - reserved for future skip semantics
    del force  # accepted but no longer needed
    for name in names:
        target_dir = root / name
        target = target_dir / "sponsio.yaml"
        target_dir.mkdir(parents=True, exist_ok=True)
        kept = _install_one(name, target)
        if kept is None:
            click.secho(f"  ✓ wrote {target}", fg="green")
        else:
            click.secho(
                f"  ✓ upgraded {target}. replaced default contracts, "
                f"kept {kept['user_contracts']} customized contract(s) "
                f"and {kept['customized']} customized entry/entries",
                fg="green",
            )
        written.append(target)

    if not written:
        sys.exit(1)

    # Surface what was just loaded so the operator knows what's now
    # enforced before flipping to enforce mode. Without this, the user
    # sees ``✓ wrote …`` and has no idea what 8 rules just landed.
    for target in written:
        name = target.parent.name
        click.echo()
        click.echo(
            _render_plugin_digest(name, target.read_text(encoding="utf-8"), target)
        )


_BUNDLE_SOURCE_PREFIX = "bundle:"


def _stamp_bundled_source(bundled_text: str, name: str) -> str:
    """Tag every shipped contract with ``source: bundle:<name>`` so a
    later ``--force`` upgrade can tell them apart from user-authored
    additions in the same file.

    Idempotent: if a contract already has a ``source`` field (e.g.
    bundles that ship with their own ``source: library:...`` tag, or
    a previously-stamped install), it's left alone.
    """
    import yaml

    doc = yaml.safe_load(bundled_text) or {}
    marker = f"{_BUNDLE_SOURCE_PREFIX}{name}"
    for agent_cfg in (doc.get("agents") or {}).values():
        if not isinstance(agent_cfg, dict):
            continue
        for c in agent_cfg.get("contracts") or []:
            if isinstance(c, dict):
                c.setdefault("source", marker)
    return yaml.safe_dump(doc, sort_keys=False)


def _install_one(name: str, target: Path) -> dict | None:
    """Install or upgrade a single bundled library at ``target``.

    Returns ``None`` for a fresh install (no prior file).  Returns a
    dict ``{"user_contracts": int, "customized": int}`` for an upgrade
    (existing file present), describing what was preserved from the
    user's customisations on top of the new bundle.

    Upgrade semantics. single-file with smart merge:

    * Every default contract is tagged ``source: bundle:<name>`` at
      install time. Anything else in the file (contracts without that
      tag, or with ``source:`` pointing elsewhere) is treated as
      user-authored.
    * On upgrade, the default section is wholesale replaced with the
      new bundle's contracts; user-authored contracts and the agent's
      ``customized:`` block are spliced back in unchanged.
    * Manual edits to a *default* contract (i.e. changing its body in
      place rather than adding a ``customized:`` entry) are wiped on
      upgrade. same model as ``brew upgrade`` over a hand-edited
      formula. The skill flow steers users to ``customized:`` for
      exactly this reason.
    """
    from sponsio.plugin.registry import read_bundled

    new_text = _stamp_bundled_source(read_bundled(name), name)

    if not target.exists():
        target.write_text(new_text, encoding="utf-8")
        return None

    import yaml

    new_doc = yaml.safe_load(new_text) or {}
    existing = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    marker = f"{_BUNDLE_SOURCE_PREFIX}{name}"
    user_contracts_kept = 0
    tweaks_kept = 0

    for agent_id, new_agent in (new_doc.get("agents") or {}).items():
        if not isinstance(new_agent, dict):
            continue
        existing_agent = (existing.get("agents") or {}).get(agent_id) or {}
        if not isinstance(existing_agent, dict):
            existing_agent = {}

        # Pull user-authored contracts from ``contracts:``. anything
        # without our bundle marker. Entries tagged with the bundle
        # marker are shipped content for THIS bundle and get dropped;
        # the new bundle's freshly stamped contracts take their place.
        # Every entry under ``contracts:`` is a contract (``E:`` plus
        # optional ``A:``); tweaks live in their own ``customized:``
        # block, handled below.
        existing_contracts = existing_agent.get("contracts") or []
        kept = [
            c
            for c in existing_contracts
            if isinstance(c, dict) and c.get("source") != marker
        ]
        if kept:
            new_agent.setdefault("contracts", []).extend(kept)
            user_contracts_kept += len(kept)

        # ``customized:`` block. always user-authored, preserve
        # verbatim on upgrade.
        existing_block = existing_agent.get("customized")
        if existing_block:
            new_agent["customized"] = existing_block
            if isinstance(existing_block, list):
                tweaks_kept += len(existing_block)

    target.write_text(yaml.safe_dump(new_doc, sort_keys=False), encoding="utf-8")
    return {"user_contracts": user_contracts_kept, "customized": tweaks_kept}


_PATTERN_LABEL = {
    "rate_limit": "Rate limits",
    "arg_blacklist": "Argument blocks",
    "arg_allowlist": "Argument allowlists",
    "must_precede": "Ordering",
    "always_followed_by": "Ordering",
    "must_confirm": "Confirmation gates",
    "no_data_leak": "Data-leak guards",
    "loop_detection": "Loop guards",
    "bounded_retry": "Retry caps",
    "cooldown": "Cooldowns",
    "scope_limit": "Scope limits",
    "arg_length_limit": "Length limits",
    "destructive_action_gate": "Destructive-action gates",
    "idempotent": "Idempotency",
    "segregation_of_duty": "Segregation of duty",
    "no_reversal": "No-reversal",
    "mutual_exclusion": "Mutual exclusion",
    "requires_permission": "Permission gates",
}


def _render_plugin_digest(
    name: str,
    yaml_text: str,
    yaml_path: Path | None = None,
) -> str:
    """Pretty-print the contracts loaded from a sponsio.yaml.

    Groups rules by friendly category (rate limits, hard denies, arg
    blocks, …) so the operator sees what the bundle actually enforces.
    Used by ``plugin install`` (for post-write reveal) and ``plugin show``
    (for ad-hoc inspection).
    """
    import yaml

    raw = yaml.safe_load(yaml_text) or {}
    agents = raw.get("agents", {})
    lines: list[str] = []

    total = sum(len(a.get("contracts", []) or []) for a in agents.values())
    header = f"  {name}. {total} contract{'s' if total != 1 else ''}"
    lines.append(click.style(header, bold=True))
    if yaml_path is not None:
        lines.append(f"  {yaml_path}")
    lines.append("")

    if total == 0:
        lines.append("  (no contracts in this library yet)")
        return "\n".join(lines)

    for agent_id, agent_cfg in agents.items():
        contracts = agent_cfg.get("contracts", []) or []
        if not contracts:
            continue
        if len(agents) > 1:
            lines.append(f"  agent: {agent_id}")

        groups: dict[str, list[str]] = {}
        for c in contracts:
            g_block = _contract_guarantee(c) or {}
            pattern = g_block.get("pattern", "?")
            args = g_block.get("args") or []
            # rate_limit with cap=0 is a hard deny. surface separately.
            if pattern == "rate_limit" and len(args) >= 2 and args[1] == 0:
                category = "Hard denies"
            else:
                category = _PATTERN_LABEL.get(pattern, pattern)
            groups.setdefault(category, []).append(c.get("desc", "(no desc)"))

        # Stable category order: hard denies first, then alphabetical.
        ordered = sorted(
            groups.keys(),
            key=lambda k: (k != "Hard denies", k.lower()),
        )
        for category in ordered:
            descs = groups[category]
            lines.append(f"  {click.style(category, fg='cyan')} ({len(descs)})")
            for d in descs:
                lines.append(f"    • {d}")
            lines.append("")

    lines.append(
        f"  Customize by adding entries to a ``customized:`` block, or appending\n"
        f"  new ``contracts:`` entries in {yaml_path or 'the file'}.\n"
        "  Don't hand-edit a default rule's body. re-running ``sponsio plugin install``\n"
        "  (or ``sponsio host install``) replaces default contracts; only ``customized:``\n"
        "  and your own ``contracts:`` entries (without a ``source: bundle:*`` tag) survive."
    )
    return "\n".join(lines)


@plugin.command(name="show")
@click.argument("name")
@click.option(
    "--root",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Override the per-plugin library root "
        "(default: $SPONSIO_PLUGIN_ROOT or ~/.sponsio/plugins)."
    ),
)
def plugin_show(name: str, root: Path | None):
    """Print a digest of contracts loaded for ``<name>``.

    After ``sponsio plugin install github``, this is the
    "what did I just get?" command. lists each rule by category
    (hard denies, rate limits, arg blocks, …) so the operator
    knows what's enforced.

    Examples:

    \b
        sponsio plugin show github               # installed library
        sponsio plugin show github --root ./tmp  # custom root
    """
    from sponsio.plugin.registry import list_bundled, read_bundled

    if root is None:
        env = os.environ.get("SPONSIO_PLUGIN_ROOT")
        root = Path(env).expanduser() if env else Path.home() / ".sponsio" / "plugins"

    yaml_path = root / name / "sponsio.yaml"
    if yaml_path.exists():
        click.echo(
            _render_plugin_digest(
                name, yaml_path.read_text(encoding="utf-8"), yaml_path
            )
        )
        return

    if name in list_bundled():
        click.secho(
            f"  {name} is not installed at {yaml_path}.\n"
            f"  Showing the bundled starter (run "
            f"`sponsio plugin install {name}` to install).\n",
            fg="yellow",
        )
        click.echo(_render_plugin_digest(name, read_bundled(name)))
        return

    click.secho(
        f"Error: no installed or bundled library named {name!r}.\n"
        f"Bundled: {', '.join(list_bundled())}",
        fg="red",
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# ``sponsio plugin append``. additive merge from a staging YAML
#
# The "host bucket without an API key" path: the host agent does the
# extraction in its own context, writes the proposed contracts to a
# transient staging file outside Zone B, then runs this command to
# merge them into ``~/.sponsio/plugins/<name>/sponsio.yaml``.
#
# Structurally additive. by construction this command can only ADD
# new contracts.  All validation + merge logic lives in
# :mod:`sponsio.plugin.append_ops` so the daemon RPC handler shares
# the exact same checks (no drift between the two callers).
#
# Two execution paths:
#
# * **Direct file mode** (no daemon running): the CLI does the merge
#   itself. fine in dev / single-user setups where the user owns
#   the host bucket file.
# * **Daemon mode** (daemon running at the resolved socket): the CLI
#   sends the staging YAML over IPC and the daemon performs the
#   merge.  This is the path that gives kernel-enforced self-modify
#   protection: in a system install the daemon runs as a separate
#   UID and the agent's user UID has no write access to the file at
#   all, so the only legitimate write goes through the daemon.
# ---------------------------------------------------------------------------


@plugin.command(name="append")
@click.option(
    "--from",
    "from_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Staging YAML file with the contracts to append.",
)
@click.option(
    "--target",
    "target_name",
    required=True,
    help=(
        "Plugin id (e.g. `_host_cursor`, `github`).  Resolves to "
        "``<root>/<target>/sponsio.yaml``."
    ),
)
@click.option(
    "--root",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Override the per-plugin library root "
        "(default: $SPONSIO_PLUGIN_ROOT or ~/.sponsio/plugins)."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the staging file's contracts as they would be appended; do not write.",
)
@click.option(
    "--no-daemon",
    is_flag=True,
    default=False,
    help=(
        "Skip the daemon route even if a daemon is reachable; do the "
        "merge in this process via direct file write.  Used for tests "
        "and dev setups where the user explicitly wants in-process behaviour."
    ),
)
def plugin_append(
    from_path: Path,
    target_name: str,
    root: Path | None,
    dry_run: bool,
    no_daemon: bool,
):
    """Atomically append agent-authored contracts to a host bucket library.

    Use this from the ``sponsio`` skill instead of ``cat staging >>
    host.yaml``: the redirect-form is denied by Zone B's self-modify
    pack on host bucket paths, while this command performs the same
    semantic add through validated, atomic Python code.

    The command is **structurally additive**:

    \b
      * Only `contracts:` entries pass through; `customized:`,
        `include:`, `tool_rename:`, etc. are rejected.
      * No `disabled:` on contracts (that's `customized:` territory).
      * Each appended contract must have a `desc:` that does not
        collide with any contract already in the target.
      * The merged file is validated via the loader before write.

    Examples:

    \b
        sponsio plugin append --from .sponsio.staging.yaml --target _host_cursor
        sponsio plugin append --from /tmp/policy-rules.yaml --target github --dry-run
    """
    from sponsio.daemon.client import DaemonClient, DaemonError, daemon_is_running
    from sponsio.plugin.append_ops import (
        AppendError,
        AppendResult,
        merge_staging_into_target,
    )

    if root is None:
        env = os.environ.get("SPONSIO_PLUGIN_ROOT")
        root = Path(env).expanduser() if env else Path.home() / ".sponsio" / "plugins"

    staging_text = from_path.read_text(encoding="utf-8")

    # Daemon route: when a daemon is reachable AND --no-daemon is not
    # set, send the merge over IPC.  This is the only write path that
    # works in a system install (where the host bucket is owned by a
    # privileged UID and direct in-process file I/O would EACCES).
    if not no_daemon and daemon_is_running():
        client = DaemonClient()
        try:
            result_dict = client.call(
                "plugin.append",
                {
                    "target": target_name,
                    "staging_yaml": staging_text,
                    "dry_run": dry_run,
                    "root": str(root),
                },
            )
        except DaemonError as e:
            # Surface the daemon's structured error code as a normal
            # CLI failure; the user shouldn't have to know about IPC.
            raise click.ClickException(f"{e} (code={e.code})") from e
        result = AppendResult(**result_dict)
    else:
        # Direct mode: dev / single-user / explicit --no-daemon.
        target = root / target_name / "sponsio.yaml"
        try:
            result = merge_staging_into_target(target, staging_text, dry_run=dry_run)
        except AppendError as e:
            raise click.ClickException(str(e)) from e

    if result.dry_run:
        click.secho(
            f"DRY RUN. would append {result.appended_count} contract(s) "
            f"to agent {result.agent_id!r} in {result.target_path}",
            fg="yellow",
        )
        for desc in result.descs:
            click.echo(f"  + {desc}")
    else:
        click.secho(
            f"✓ appended {result.appended_count} contract(s) to agent "
            f"{result.agent_id!r} in {result.target_path}",
            fg="green",
        )


@plugin.command(name="scan")
@click.argument(
    "plugin_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=False,
)
@click.option(
    "--plugin-id",
    "plugin_id_override",
    default="",
    help=(
        "Explicit plugin id when scanning a bare MCP server (no "
        "Claude Code .claude-plugin/plugin.json wrapping it).  "
        "Required when no plugin_dir is given or it lacks a manifest."
    ),
)
@click.option(
    "--tools",
    "-t",
    "tools_csv",
    default="",
    help=(
        "Comma-separated tool names the plugin exposes (e.g. "
        "`mcp__github__create_issue,mcp__github__list_repos`). Use "
        "``--introspect`` to query the MCP server directly instead."
    ),
)
@click.option(
    "--introspect",
    "introspect_cmd",
    default="",
    help=(
        "Spawn an MCP server with this command and call ``tools/list`` "
        "to auto-populate the tool inventory.  Example: "
        "``--introspect 'python3 server.py'``.  Mutually exclusive "
        "with ``--tools``; takes precedence when both are given."
    ),
)
@click.option(
    "--introspect-env",
    "introspect_env",
    multiple=True,
    help=(
        "Environment variable for the introspected server, repeatable: "
        "``--introspect-env API_KEY=xxx --introspect-env LOG=/tmp/x``."
    ),
)
@click.option(
    "--target-host",
    type=click.Choice(["claude-code", "openclaw"]),
    default="claude-code",
    show_default=True,
    help=(
        "Which host runtime will load the generated library.  Determines "
        "how introspected MCP tool names are namespaced: claude-code "
        "prefixes them as ``mcp__<plugin-id>__<tool>`` (matching what "
        "Claude Code surfaces); openclaw keeps them flat."
    ),
)
@click.option(
    "--root",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Override the per-plugin library root "
        "(default: $SPONSIO_PLUGIN_ROOT or ~/.sponsio/plugins)."
    ),
)
@click.option(
    "--apply/--no-apply",
    default=False,
    help="Write the library to <root>/<plugin-id>/sponsio.yaml.",
)
@click.option(
    "--no-runaway",
    is_flag=True,
    default=False,
    help="Skip the default `sponsio:core/runaway` include.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="With --apply, overwrite an existing library file.",
)
def plugin_scan(
    plugin_dir: Path | None,
    plugin_id_override: str,
    tools_csv: str,
    introspect_cmd: str,
    introspect_env: tuple[str, ...],
    target_host: str,
    root: Path | None,
    apply: bool,
    no_runaway: bool,
    force: bool,
):
    """Generate a starter contract library from a host plugin.

    Reads ``<plugin-dir>/.claude-plugin/plugin.json`` (Claude Code) or
    ``<plugin-dir>/openclaw.plugin.json`` (OpenClaw), optionally
    ``.mcp.json`` and ``skills/`` for context, then runs name-heuristic
    rule generation on every tool. either listed via ``--tools`` or
    auto-discovered via ``--introspect`` against a running MCP server.

    Defaults to dry-run (prints the YAML); use ``--apply`` to write it.
    """
    from sponsio.plugin.scan import (
        ManifestError,
        scan_plugin,
        synthesize_manifest,
    )

    declared_tools: list[str] = []
    introspected_tools: list = []  # ToolInfo objects (used by --llm)
    if introspect_cmd:
        from sponsio.plugin.mcp_introspect import (
            IntrospectError,
            introspect_mcp_server,
        )
        import shlex

        env_dict: dict[str, str] = {}
        for kv in introspect_env:
            if "=" not in kv:
                click.secho(f"--introspect-env expects KEY=VALUE, got {kv!r}", fg="red")
                sys.exit(2)
            k, _, v = kv.partition("=")
            env_dict[k] = v

        cmd = shlex.split(introspect_cmd)
        click.echo(f"# introspecting via: {' '.join(cmd)}")
        try:
            tools = introspect_mcp_server(cmd, env=env_dict)
        except IntrospectError as e:
            click.secho(f"introspect failed: {e}", fg="red")
            sys.exit(1)
        introspected_tools = tools
        # Namespace tool names per the target host runtime.  Claude
        # Code surfaces MCP tools as ``mcp__<plugin-id>__<tool>``;
        # OpenClaw keeps them flat.  Without this, scan would route
        # all tools to ``_host`` (the fallback) instead of the
        # plugin-id directory.
        canonical_names = [t.name for t in tools]
        if target_host == "claude-code":
            ns = plugin_id_override or (plugin_dir.name if plugin_dir else "")
            if not ns:
                click.secho(
                    "--introspect with --target-host claude-code needs a plugin-id "
                    "(via --plugin-id or by passing a plugin_dir).",
                    fg="red",
                )
                sys.exit(2)
            declared_tools = [f"mcp__{ns}__{n}" for n in canonical_names]
        else:
            declared_tools = list(canonical_names)
        click.echo(
            f"# discovered {len(canonical_names)} tools: "
            f"{', '.join(canonical_names) or '(none)'}"
        )
        if target_host == "claude-code" and canonical_names:
            click.echo(f"# namespaced for claude-code: {', '.join(declared_tools)}")
        if tools_csv.strip():
            click.secho(
                "# (--tools ignored; --introspect takes precedence)",
                fg="yellow",
            )
    else:
        declared_tools = [t.strip() for t in tools_csv.split(",") if t.strip()]

    # Synthesize a manifest when we're scanning a bare MCP server (no
    # Claude Code wrapping plugin). operator passes --introspect and
    # --plugin-id; no .claude-plugin/plugin.json needed.
    synthetic_manifest = None
    plugin_dir_has_manifest = (
        plugin_dir is not None
        and (plugin_dir / ".claude-plugin" / "plugin.json").exists()
    )
    if not plugin_dir_has_manifest:
        if not plugin_id_override:
            click.secho(
                "scan needs either:\n"
                "  - a Claude Code plugin dir (with .claude-plugin/plugin.json), or\n"
                "  - --plugin-id <id> when scanning a bare MCP server.",
                fg="red",
            )
            sys.exit(2)
        synthetic_manifest = synthesize_manifest(plugin_id_override)
        if plugin_dir is None:
            # We still pass plugin_dir=None into scan_plugin; manifest
            # override carries everything needed.
            plugin_dir = None
        click.echo(f"# using synthesized manifest for plugin_id={plugin_id_override!r}")
    try:
        result = scan_plugin(
            plugin_dir,
            declared_tools=declared_tools,
            include_runaway=not no_runaway,
            manifest=synthetic_manifest,
        )
    except ManifestError as e:
        click.secho(f"scan failed: {e}", fg="red")
        sys.exit(1)

    click.echo(f"# plugin id:       {result.manifest.plugin_id}")
    click.echo(f"# tools applied:   {len(result.declared_tools)}")
    click.echo(
        f"# library groups:  "
        f"{', '.join(g.plugin_id for g in result.groups) or '(none)'}"
    )
    if result.manifest.mcp_servers:
        click.echo(f"# MCP servers:     {', '.join(result.manifest.mcp_servers)}")
    if result.manifest.skill_names:
        click.echo(f"# skills:          {', '.join(result.manifest.skill_names)}")

    if not apply:
        for g in result.groups:
            click.echo("")
            click.echo(
                f"# === library group: {g.plugin_id} "
                f"({len(g.tools)} tools, {len(g.proposed)} rules) ==="
            )
            click.echo(g.library_yaml)
        # When ``--introspect`` was used, dump the full tool inventory
        # (name + description + inputSchema) as JSON.  This is what a
        # host agent driving the setup skill needs to apply the
        # contract-extraction prompt. heuristic rules cover the
        # obvious cases; the agent fills semantic gaps using the
        # description + schema fields its own LLM context can read.
        if introspected_tools:
            click.echo("")
            click.echo(
                f"# === tool inventory (target_host={target_host}, "
                f"plugin_id={result.manifest.plugin_id}) ==="
            )
            click.echo("# JSON below is parsable by the host agent for the")
            click.echo("# contract-extraction prompt at:")
            click.echo(f"#     sponsio plugin prompt {target_host}")
            tools_json = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    **(
                        {
                            "tool_name_in_contracts": f"mcp__{result.manifest.plugin_id}__{t.name}"
                        }
                        if target_host == "claude-code"
                        else {"tool_name_in_contracts": t.name}
                    ),
                }
                for t in introspected_tools
            ]
            click.echo(
                json.dumps(
                    {
                        "plugin_id": result.manifest.plugin_id,
                        "target_host": target_host,
                        "tools": tools_json,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        click.echo(
            "\n(dry-run. re-run with --apply to write each group to "
            "<root>/<group>/sponsio.yaml)"
        )
        return

    if root is None:
        env = os.environ.get("SPONSIO_PLUGIN_ROOT")
        root = Path(env).expanduser() if env else Path.home() / ".sponsio" / "plugins"

    written: list[Path] = []
    for g in result.groups:
        target_dir = root / g.plugin_id
        target = target_dir / "sponsio.yaml"
        if target.exists() and not force:
            click.secho(
                f"  skipped {target}: already exists "
                f"(re-run with --force to overwrite)",
                fg="yellow",
            )
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(g.library_yaml, encoding="utf-8")
        written.append(target)
        click.secho(f"  ✓ wrote {target}", fg="green")

    if not written and not force:
        sys.exit(1)


@plugin.command(name="prompt")
@click.argument(
    "target_host",
    type=click.Choice(["claude-code", "openclaw", "mcp-bare"]),
)
def plugin_prompt(target_host: str):
    """Print the contract-extraction prompt template for a target host.

    The setup skill drives a host agent (Claude Code or OpenClaw)
    through a four-step workflow:

      1. ``sponsio plugin scan --introspect "..."`` to get the tool
         inventory (description + inputSchema).
      2. ``sponsio plugin prompt <host>`` (this command) to get the
         prompt template for the target host.
      3. The agent applies the prompt to the inventory using its own
         LLM context. no separate API call.
      4. The agent writes the resulting YAML to
         ``~/.sponsio/plugins/<plugin-id>/sponsio.yaml``.

    Three templates ship: claude-code (mcp__-prefixed tool names),
    openclaw (flat names), mcp-bare (no host-specific assumptions).

    Output goes to stdout. pipe to a file or capture via the agent.
    """
    from importlib.resources import files

    pkg = files("sponsio.plugin.prompts")
    main = pkg.joinpath(f"{target_host}.md").read_text(encoding="utf-8")
    vocab = pkg.joinpath("_pattern_vocabulary.md").read_text(encoding="utf-8")
    # Substitute the vocabulary section in place of the marker the
    # template files reference.  Single source of truth for the
    # pattern names + arg shapes; updates ripple to every host.
    marker = "(Loaded from `_pattern_vocabulary.md`. use ONLY those patterns.)"
    if marker in main:
        click.echo(main.replace(marker, vocab))
    else:
        # Backward-safe fallback if a template forgets the marker.
        click.echo(main)
        click.echo("")
        click.echo(vocab)


@plugin.command(name="guard")
@click.option(
    "--stdin",
    "use_stdin",
    is_flag=True,
    default=True,
    help=(
        "Read a single hook event as JSON from stdin (Claude Code "
        "PreToolUse / PostToolUse protocol)."
    ),
)
def plugin_guard(use_stdin: bool):
    """Plugin-system hook entry point. evaluates one tool call.

    Wired into a Claude Code plugin via ``hooks/hooks.json``::

        {
          "hooks": {
            "PreToolUse": [
              {"matcher": "*",
               "hooks": [{"type": "command",
                          "command": "sponsio plugin guard --stdin"}]}
            ]
          }
        }

    Reads the event JSON from stdin, derives the plugin id from the
    tool name (``Bash`` → ``_host``; ``acme:fetch`` → ``acme``;
    ``mcp__acme__fetch`` → ``acme``), loads the matching library at
    ``~/.sponsio/plugins/<plugin>/sponsio.yaml`` (override with
    ``$SPONSIO_PLUGIN_ROOT``), and writes the deny / allow reply that
    Claude Code expects.

    Exits 0 in every code path: a Sponsio bug must never wedge an
    agent's tool call. Diagnostics go to stderr; deny verdicts go to
    stdout in the documented hook reply schema.
    """
    from sponsio.guard_stdin import run_stdin

    sys.exit(run_stdin())
