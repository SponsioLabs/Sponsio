"""Sponsio CLI entry point."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import click

from sponsio.cli.app import cli
from sponsio.constants import DASHBOARD_DEFAULT_PORT


def _contract_guarantee(entry):
    """Read the guarantee block out of a YAML/dict contract entry.

    Reads the canonical ``G`` (short) / ``guarantee`` (long) keys. No
    legacy alias support. the rename is hard.
    """
    if not isinstance(entry, dict):
        return None
    return entry.get("G") or entry.get("guarantee")


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--scenario",
    default="cleanup",
    type=click.Choice(["cleanup", "backup", "wire", "freeze"], case_sensitive=False),
    help="Demo scenario: cleanup (default), backup, wire, freeze",
)
@click.option(
    "--mode",
    default="mock",
    type=click.Choice(["mock", "integration"], case_sensitive=False),
    show_default=True,
    help="mock uses no optional SDKs; integration runs repo example scripts.",
)
@click.option("--no-guard", is_flag=True, help="Replay the unsafe trajectory.")
@click.option("--fast", is_flag=True, help="Skip typing delays.")
def demo(scenario: str, mode: str, no_guard: bool, fast: bool):
    """Run a Sponsio demo in your terminal.

    Four trajectory replays showing unsafe agent behavior and the
    contracts that block it. The default mock mode works from a plain
    PyPI install with no API key and no optional framework SDKs.

    \b
      cleanup . Claude Code cleanup agent deletes `.env` & `.git/`
      backup  . SRE cost-optimizer deletes prod DR backups (OWASP ASI-10)
      wire    . AP copilot wires $847k to an unverified vendor (OWASP ASI-09)
      freeze  . Replit-style agent violates code freeze + hides it (OWASP ASI-10)

    Examples:\n
        sponsio demo\n
        sponsio demo --scenario freeze --fast\n
        sponsio demo --scenario wire --no-guard\n
        sponsio demo --mode integration --scenario freeze
    """
    scenario_map = {
        "cleanup": ("demo_coding_cleanup.py", "Coding Agent \u2014 Cleanup gone rogue"),
        "backup": (
            "demo_backup_delete.py",
            "SRE Cost-Optimizer \u2014 Prod DR backups deleted",
        ),
        "wire": (
            "demo_wire_transfer.py",
            "AP Copilot \u2014 Fraudulent wire transfer",
        ),
        "freeze": (
            "demo_freeze_violation.py",
            "Coding Agent \u2014 Code-freeze violation + coverup",
        ),
    }

    script_name, label = scenario_map[scenario]

    click.echo()
    click.echo(click.style("Sponsio Demo", bold=True))
    click.echo(click.style(f"  {label}", fg="cyan"))
    click.echo()

    if mode == "mock":
        from sponsio.demos.replay import run_demo

        run_demo(scenario, no_guard=no_guard, fast=fast)
        return

    import sponsio

    # Resolve relative to the installed package, not this file's depth,
    # so it stays correct regardless of where the CLI code lives.
    repo_root = Path(sponsio.__file__).resolve().parent.parent
    script_path = repo_root / "examples" / "demo" / script_name

    if not script_path.exists():
        click.echo(
            click.style(
                "Error: integration demo scripts are only available from a "
                "source checkout. Use the default mock mode from PyPI: "
                f"{click.style('sponsio demo', bold=True)}",
                fg="red",
            )
        )
        sys.exit(1)

    try:
        cmd = [sys.executable, str(script_path)]
        if no_guard:
            cmd.append("--no-guard")
        if fast:
            cmd.append("--fast")
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        click.echo("\nInterrupted.")


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------


@cli.command()
def patterns():
    """List all available contract patterns with examples."""

    def _section(title, items, color):
        click.echo(click.style(title, bold=True))
        click.echo()
        for name, example, meaning in items:
            click.echo(click.style(f"  {name}", fg=color, bold=True))
            click.echo(f"    Example : {example}")
            click.echo(click.style(f"    Meaning : {meaning}", dim=True))
            click.echo()

    # --- Core temporal (14) ---
    click.echo()
    _section(
        "Core Temporal Patterns (14 det)",
        [
            ("must_precede", "tool `A` must precede `B`", "A must happen before B"),
            (
                "always_followed_by",
                "tool `A` must always be followed by `B`",
                "whenever A, eventually B",
            ),
            ("no_reversal", "cannot `B` after `A`", "A commits; B forbidden after"),
            (
                "requires_permission",
                "tool `X` requires permission `perm`",
                "tool needs authorization",
            ),
            ("no_data_leak", "no data leak from `src` to `ext`", "data containment"),
            (
                "mutual_exclusion",
                "`A` and `B` are mutually exclusive",
                "at most one per session",
            ),
            ("rate_limit", "tool `X` at most N times", "frequency cap"),
            ("idempotent", "tool `X` must execute at most once", "single execution"),
            (
                "deadline",
                "`action` within N steps of `trigger`",
                "time-bounded obligation",
            ),
            ("must_confirm", "tool `X` requires confirmation", "human-in-the-loop"),
            ("cooldown", "N steps between consecutive `X`", "minimum interval"),
            (
                "segregation_of_duty",
                "review and approve by different agents",
                "separation of concerns",
            ),
            ("bounded_retry", "tool `X` limited to N retries", "retry cap"),
            (
                "loop_detection",
                "tool `X` at most N consecutive calls",
                "runaway loop prevention",
            ),
        ],
        "cyan",
    )

    # --- Argument / path / length (5) ---
    _section(
        "Argument & Path Constraints (5 det)",
        [
            (
                "arg_blacklist",
                "tool `bash` arg `command` must not match `rm -rf`",
                "forbid patterns in args",
            ),
            (
                "arg_allowlist",
                "tool `send_money` arg `recipient` must be one of `US-internal-001`, `US-internal-002`",
                "arg must match one of the allowed patterns",
            ),
            (
                "scope_limit",
                "tool `file_write` restricted to `/app/data`",
                "restrict tool to allowed paths",
            ),
            (
                "arg_length_limit",
                "tool `bash` arg `command` max 500 chars",
                "block code-injection via long args",
            ),
            (
                "data_intact",
                "`grep` must use only original data files",
                "tool must use unmodified data",
            ),
        ],
        "cyan",
    )

    # --- OWASP Agentic Top 10 (8) ---
    _section(
        "OWASP Agentic Security Patterns (8 det)",
        [
            (
                "destructive_action_gate",
                "`delete_db` requires approval from `approver`",
                "human approval + role for destructive ops",
            ),
            (
                "untrusted_source_gate",
                "after `web_fetch`, `send_email` requires re-confirmation",
                "re-confirm after untrusted input (A,E pair)",
            ),
            (
                "required_steps_completion",
                "every `start_task` must be followed by all of [`log`, `notify`]",
                "all steps must follow trigger",
            ),
            (
                "tool_allowlist",
                "only [`read_file`, `write_file`] may be called",
                "first-line defense against injected tools",
            ),
            (
                "dangerous_bash_commands",
                "ban `rm -rf`, `sudo`, `chmod` in bash",
                "preset: dangerous shell commands",
            ),
            (
                "dangerous_sql_verbs",
                "ban `DROP`, `TRUNCATE` in `execute_sql`",
                "preset: dangerous SQL verbs",
            ),
            (
                "irreversible_once",
                "`deploy_production` at most once per session",
                "irreversible action protection",
            ),
            (
                "confirm_after_source",
                "after `fetch_url`, `file_write` requires confirmation",
                "narrow source→action gate (A,E pair)",
            ),
        ],
        "cyan",
    )

    # --- Atom extensions (3) ---
    _section(
        "Resource & Delegation Constraints (3 det)",
        [
            (
                "token_budget",
                "session total tokens must not exceed 100000",
                "limit token consumption",
            ),
            (
                "arg_value_range",
                "tool `set_price` field `amount` in [0, 1000]",
                "constrain numeric arguments",
            ),
            (
                "delegation_depth_limit",
                "delegation chain max depth 3",
                "limit agent-to-agent delegation",
            ),
        ],
        "cyan",
    )

    # --- Workflow hygiene (6) ---
    _section(
        "Workflow Hygiene Patterns (6 det)",
        [
            (
                "dry_run_before_commit",
                "`plan_migration` dry-run before `apply_migration`",
                "require dry-run before committing changes",
            ),
            (
                "backup_before_destructive",
                "`snapshot_db` before destructive `drop_table`",
                "require backup before destructive action",
            ),
            (
                "audit_after",
                "`transfer_funds` must be followed by `audit_transfer`",
                "require audit/log after sensitive action",
            ),
            (
                "approval_freshness",
                "`approve_deploy` authorizes `deploy` for 3 steps",
                "expire old approvals after N steps",
            ),
            (
                "sanitized_before_sink",
                "`web_fetch` then `sanitize_input` before `send_email`",
                "sanitize untrusted source before sink",
            ),
            (
                "duplicate_call_limit",
                "`search` args matching `invoice-42` at most 2 times",
                "cap repeated same-argument calls",
            ),
        ],
        "cyan",
    )

    # This build ships only deterministic patterns. Stochastic /
    # LLM-judged evaluators (tone, relevance, generic LLM judge, ...)
    # are an extension point with no implementation included;
    # ``sponsio patterns`` shows det only.


# ---------------------------------------------------------------------------
# packs. list the contract packs that ship inside the distribution
# ---------------------------------------------------------------------------


@cli.command()
def packs():
    """List shipped contract packs with rule counts + include syntax.

    Useful right after ``sponsio scan`` / ``sponsio onboard``: the
    generated :file:`sponsio.yaml` references packs by ``include:``
    spec, and this command prints the full inventory plus one-line
    summaries so users can see what's been pulled in without opening
    five YAML files.
    """
    # We walk the shipped contracts directory rather than hardcoding
    # a table so new packs become visible the moment they're added.
    from collections import Counter
    from importlib.resources import files

    import yaml as _yaml

    try:
        contracts_root = files("sponsio") / "contracts"
    except (ModuleNotFoundError, FileNotFoundError):
        click.echo("error: sponsio package not found on import path", err=True)
        raise SystemExit(1) from None

    rows = []  # (spec, desc_line, n_contracts, kinds_summary, needs_workspace)
    for category_dir in sorted(contracts_root.iterdir()):
        if not category_dir.is_dir():
            continue
        for pack_file in sorted(category_dir.iterdir()):
            if not pack_file.is_file() or pack_file.suffix not in (".yaml", ".yml"):
                continue
            spec = f"sponsio:{category_dir.name}/{pack_file.stem}"
            try:
                text = pack_file.read_text(encoding="utf-8")
                doc = _yaml.safe_load(text) or {}
                # Header comment's first meaningful sentence gives the
                # summary.  Fallback to "(no summary)" if the pack didn't
                # follow the convention.
                summary = "(no summary)"
                for line in text.splitlines():
                    stripped = line.lstrip("#").strip()
                    if not stripped or stripped.startswith("="):
                        continue
                    if stripped.startswith("sponsio/contracts/"):
                        continue
                    summary = stripped
                    break

                agents = doc.get("agents") or {}
                template = agents.get("*") or next(iter(agents.values()), {})
                contracts = (template or {}).get("contracts") or []
                n = len(contracts)

                # Rough kind count. det patterns vs raw LTL.  OSS ships
                # no sto pipeline; the third bucket is gone.
                kinds = Counter()
                for c in contracts:
                    es = _contract_guarantee(c)
                    if isinstance(es, dict):
                        es_list = [es]
                    elif isinstance(es, list):
                        es_list = es
                    else:
                        es_list = []
                    for e in es_list:
                        if not isinstance(e, dict):
                            continue
                        if "ltl" in e and "pattern" not in e:
                            kinds["raw"] += 1
                        elif e.get("pattern"):
                            kinds["det"] += 1

                needs_ws = "<workspace>/" in text
                rows.append((spec, summary, n, dict(kinds), needs_ws))
            except Exception as exc:  # noqa: BLE001
                rows.append((spec, f"(unreadable: {exc})", 0, {}, False))

    click.echo()
    click.echo(click.style("Shipped contract packs", bold=True))
    click.echo()
    for spec, summary, n, kinds, needs_ws in rows:
        badge = " [needs workspace:]" if needs_ws else ""
        click.echo(click.style(f"  {spec}{badge}", fg="cyan", bold=True))
        k = ", ".join(f"{v} {k}" for k, v in kinds.items()) or f"{n} contracts"
        click.echo(f"    {n} contracts ({k})")
        click.echo(click.style(f"    {summary}", dim=True))
        click.echo()
    click.echo("Use in sponsio.yaml:")
    click.echo("  agents:")
    click.echo("    your_agent:")
    click.echo("      include:")
    for spec, *_ in rows:
        click.echo(f"        - {spec}")


# ---------------------------------------------------------------------------
# skill. install the bundled Agent Skill into Cursor / Claude Code / Codex
# ---------------------------------------------------------------------------


@cli.group()
def skill():
    """Install / manage the bundled Sponsio Agent Skill.

    Sponsio ships an Agent Skill (``SKILL.md``) that teaches Cursor,
    Claude Code, and Codex how to run the ``onboard``/``scan``/``report``
    lifecycle end-to-end.  The source file lives inside the installed
    package at ``sponsio/skills/sponsio/SKILL.md``; this subcommand
    puts it where the respective coding agent will discover it.

    The canonical source is packaged, not developer-local, so:

    * ``pip install sponsio`` → ``sponsio skill install`` works.
    * Upgrading Sponsio refreshes the skill via pip; re-run
      ``sponsio skill install`` (or use ``--link`` once) to propagate.
    """


# Per-tool discovery paths.  Keep the mapping in one place so
# ``--tool both`` / ``auto`` can iterate over it without duplicating
# knowledge about where each tool looks.
_SKILL_TOOL_DIRS: dict[str, Path] = {
    "cursor": Path("~/.cursor/skills").expanduser(),
    "claude": Path("~/.claude/skills").expanduser(),
    "codex": Path("~/.codex/skills").expanduser(),
}


def _packaged_skill_source() -> Path:
    """Return the absolute path to the packaged ``sponsio/skills/sponsio/``
    directory.  Raises ``FileNotFoundError`` if the install is missing
    the skill. which means a broken wheel or a dev checkout without
    ``pip install -e`` (common footgun)."""
    from importlib.resources import files

    try:
        src = Path(str(files("sponsio") / "skills" / "sponsio"))
    except (ModuleNotFoundError, FileNotFoundError) as exc:  # pragma: no cover
        raise FileNotFoundError(
            "sponsio/skills/sponsio/ not found in the installed package. "
            "If you're running from a source checkout, `pip install -e .` "
            "first so package-data is registered."
        ) from exc
    if not src.is_dir() or not (src / "SKILL.md").is_file():
        raise FileNotFoundError(
            f"Expected {src / 'SKILL.md'} to exist but it doesn't. "
            "The sponsio wheel may be incomplete. re-install sponsio."
        )
    return src


def _detect_installed_tools() -> list[str]:
    """Return the list of tools whose personal-skills dir already exists.

    Used by ``--tool auto``.  We prefer "dir already exists" over
    "tool is installed" because the dir is a stronger signal of "the
    user actually uses this tool's skill system". Cursor / Claude
    Code both create it on first skill install.
    """
    return [name for name, path in _SKILL_TOOL_DIRS.items() if path.is_dir()]


# ---------------------------------------------------------------------------
# Shared skill-install verification
# ---------------------------------------------------------------------------
#
# Both ``sponsio skill install`` (post-write footer) and
# ``sponsio doctor`` (skill health check) need to answer the same
# question: "is the skill installed at ``<parent>/sponsio/`` such that
# a coding-agent can actually discover it?".  A positive answer
# requires all of:
#
#   1. The subdir ``<parent>/sponsio/`` exists.
#   2. It contains ``SKILL.md``, non-empty.
#   3. That file starts with ``---`` (YAML frontmatter delimiter).
#   4. Frontmatter contains ``name: sponsio``. the discovery key the
#      agent dispatchers look up.
#   5. For non-symlink installs, content matches the currently-
#      packaged skill. otherwise ``pip install -U sponsio`` has
#      moved ahead of the copy and the user should re-install.
#
# We encode this once in ``_verify_skill_install_target`` and use it
# from both places.  Status is one of:
#   - ``ok``      : healthy, up to date
#   - ``drift``   : installed but stale (copy lagging packaged src)
#   - ``missing`` : nothing at this target (neither installed nor broken)
#   - ``broken``  : directory exists but SKILL.md is unusable
SkillInstallStatus = Literal["ok", "drift", "missing", "broken"]


@dataclass
class _SkillInstallHealth:
    """Result of probing one skill-target location."""

    tool: str  # "cursor" / "claude" / "codex" / "custom:<abs>"
    parent: Path  # e.g. ~/.cursor/skills
    skill_md: Path  # e.g. ~/.cursor/skills/sponsio/SKILL.md
    mode: Literal["link", "copy", "missing", "broken"]
    status: SkillInstallStatus
    detail: str  # human summary; safe to drop into click.echo()


def _hash_file(p: Path) -> str | None:
    """md5 of ``p``'s bytes, or ``None`` if unreadable.

    md5 is fine here. we're checking equality of two local files we
    control, not resisting adversarial collisions."""
    try:
        return hashlib.md5(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _verify_skill_install_target(
    tool: str, parent: Path, packaged_src: Path
) -> _SkillInstallHealth:
    """Probe one install location and classify it.

    ``packaged_src`` is the directory returned by
    :func:`_packaged_skill_source`. typically the ``sponsio/skills/sponsio/``
    inside the wheel.  We compare the installed ``SKILL.md`` bytes
    against ``packaged_src / 'SKILL.md'`` to detect copy-drift.
    """

    target = parent / "sponsio"
    skill_md = target / "SKILL.md"

    if not target.exists() and not target.is_symlink():
        return _SkillInstallHealth(
            tool=tool,
            parent=parent,
            skill_md=skill_md,
            mode="missing",
            status="missing",
            detail=f"not installed at {skill_md}",
        )

    is_link = target.is_symlink()
    mode: Literal["link", "copy", "broken"] = "link" if is_link else "copy"

    if not skill_md.is_file():
        return _SkillInstallHealth(
            tool=tool,
            parent=parent,
            skill_md=skill_md,
            mode="broken",
            status="broken",
            detail=f"{target} exists but SKILL.md is missing. re-run with --force",
        )

    try:
        body = skill_md.read_text(errors="replace")
    except OSError as exc:
        return _SkillInstallHealth(
            tool=tool,
            parent=parent,
            skill_md=skill_md,
            mode=mode,
            status="broken",
            detail=f"{skill_md}: {exc}",
        )

    # Fast content-shape checks. catch empty / truncated / wrong-file
    # cases before we get into drift comparison.  ``name: sponsio`` is
    # what the coding-agent dispatchers grep for.
    if not body.strip():
        return _SkillInstallHealth(
            tool=tool,
            parent=parent,
            skill_md=skill_md,
            mode=mode,
            status="broken",
            detail=f"{skill_md} is empty",
        )
    if not body.startswith("---"):
        return _SkillInstallHealth(
            tool=tool,
            parent=parent,
            skill_md=skill_md,
            mode=mode,
            status="broken",
            detail=f"{skill_md} has no YAML frontmatter (agent won't discover it)",
        )
    if "name: sponsio" not in body:
        return _SkillInstallHealth(
            tool=tool,
            parent=parent,
            skill_md=skill_md,
            mode=mode,
            status="broken",
            detail=f"{skill_md} frontmatter missing `name: sponsio`. agent won't dispatch",
        )

    # Symlinks are always fresh by definition. no drift check needed.
    if is_link:
        return _SkillInstallHealth(
            tool=tool,
            parent=parent,
            skill_md=skill_md,
            mode="link",
            status="ok",
            detail=f"symlink → {packaged_src}",
        )

    # Copy: compare bytes with packaged source.  Hash mismatch means
    # the user upgraded sponsio (pip install -U) but didn't re-run
    # ``sponsio skill install``. their agent still sees the old skill.
    installed_hash = _hash_file(skill_md)
    packaged_hash = _hash_file(packaged_src / "SKILL.md")
    if (
        installed_hash is not None
        and packaged_hash is not None
        and installed_hash != packaged_hash
    ):
        return _SkillInstallHealth(
            tool=tool,
            parent=parent,
            skill_md=skill_md,
            mode="copy",
            status="drift",
            detail=(
                "installed copy doesn't match packaged SKILL.md. "
                "re-run `sponsio skill install --force` after upgrading sponsio"
            ),
        )

    size = skill_md.stat().st_size
    return _SkillInstallHealth(
        tool=tool,
        parent=parent,
        skill_md=skill_md,
        mode="copy",
        status="ok",
        detail=f"copy ({size:,} bytes, in sync)",
    )


def _print_skill_discovery_footer(
    results: list[_SkillInstallHealth],
) -> bool:
    """Render the "Discovery:" block after ``sponsio skill install``.

    Returns ``True`` iff every result is ``ok``. the caller uses this
    to decide the command exit status (healthy installs → 0, any
    broken or drift → 1 so CI / scripts notice).
    """

    click.echo()
    click.echo(click.style("Discovery:", bold=True))

    all_ok = True
    for r in results:
        if r.status == "ok":
            icon = click.style("✓", fg="green", bold=True)
        elif r.status == "drift":
            icon = click.style("⚠", fg="yellow", bold=True)
            all_ok = False
        elif r.status == "missing":
            icon = click.style("·", fg="bright_black", bold=True)
            # ``missing`` here means the caller decided to install at
            # this target but the target wasn't actually written; this
            # shouldn't happen on the happy path, so surface it.
            all_ok = False
        else:  # broken
            icon = click.style("✗", fg="red", bold=True)
            all_ok = False
        click.echo(f"  {icon} {r.tool}  {r.skill_md} . {r.detail}")

    return all_ok


@skill.command("install")
@click.option(
    "--tool",
    type=click.Choice(["cursor", "claude", "codex", "both", "all", "auto"]),
    default="auto",
    show_default=True,
    help=(
        "Which coding agent's skill directory to install into.  "
        "``auto`` detects which of ``~/.cursor/skills``, "
        "``~/.claude/skills``, ``~/.codex/skills`` already exists and "
        "installs into every one that does (falls back to cursor+claude "
        "when none do).  ``both`` = cursor+claude only.  ``all`` = all "
        "three."
    ),
)
@click.option(
    "--link/--copy",
    "use_link",
    default=False,
    help=(
        "``--copy`` (default) makes a standalone copy under "
        "``<dest>/sponsio/``; safer cross-platform but requires "
        "re-running this command after ``pip install -U sponsio``. "
        "``--link`` symlinks back to the bundled skill so upgrades "
        "propagate automatically; not reliable on Windows (auto-"
        "downgraded to copy)."
    ),
)
@click.option(
    "--dest",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Install to an explicit directory instead of the per-tool "
        "default.  The skill is placed under ``<dest>/sponsio/``."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing ``<dest>/sponsio/`` entry.",
)
def skill_install(tool: str, use_link: bool, dest: Path | None, force: bool):
    mode = "link" if use_link else "copy"
    """Install the bundled Sponsio Agent Skill into a coding-agent's
    skills directory.

    Examples:\n
        sponsio skill install\n
        sponsio skill install --tool claude\n
        sponsio skill install --tool all --link\n
        sponsio skill install --dest /custom/path --force
    """
    import shutil

    src = _packaged_skill_source()

    # Resolve target directories.
    if dest is not None:
        dest = dest.expanduser().resolve()
        targets = [(f"custom:{dest}", dest)]
    else:
        if tool == "auto":
            detected = _detect_installed_tools()
            if detected:
                names = detected
            else:
                # Nothing detected. pick a sensible default pair rather
                # than erroring.  Most Cursor/Claude users will have
                # one of these even if the dir hasn't been created yet
                # (first-time install case).
                names = ["cursor", "claude"]
                click.echo(
                    click.style(
                        "· no existing skills dir detected. installing "
                        "into cursor + claude defaults",
                        fg="bright_black",
                        dim=True,
                    ),
                    err=True,
                )
        elif tool == "both":
            names = ["cursor", "claude"]
        elif tool == "all":
            names = ["cursor", "claude", "codex"]
        else:
            names = [tool]
        targets = [(name, _SKILL_TOOL_DIRS[name]) for name in names]

    if mode == "link" and sys.platform.startswith("win"):
        click.echo(
            click.style(
                "warning: --link isn't reliable on Windows; falling back to --copy",
                fg="yellow",
            ),
            err=True,
        )
        mode = "copy"

    any_written = False
    for label, parent in targets:
        target = parent / "sponsio"
        parent.mkdir(parents=True, exist_ok=True)

        if target.exists() or target.is_symlink():
            if not force:
                click.echo(
                    click.style("✗ ", fg="yellow")
                    + f"{label}: {target} already exists. pass --force to replace",
                    err=True,
                )
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)

        if mode == "link":
            try:
                target.symlink_to(src, target_is_directory=True)
            except OSError as exc:
                click.echo(
                    click.style("✗ ", fg="red")
                    + f"{label}: symlink failed ({exc}); retry with --copy",
                    err=True,
                )
                continue
            click.echo(
                click.style("✓ ", fg="green") + f"{label}: linked {target} → {src}"
            )
        else:
            shutil.copytree(src, target)
            click.echo(click.style("✓ ", fg="green") + f"{label}: copied to {target}")
        any_written = True

    if not any_written:
        raise SystemExit(1)

    # Verify every target we wrote to. catches cases where the copy
    # landed at the wrong depth (``<parent>/SKILL.md`` instead of
    # ``<parent>/sponsio/SKILL.md``), the source wheel is broken, or a
    # filesystem quirk silently ate the write.  Also gives the user a
    # concrete path to paste into their agent's logs if discovery
    # later fails.
    probes = [
        _verify_skill_install_target(label, parent, src) for label, parent in targets
    ]
    # ``--force`` can leave ``mode == "missing"`` for slots the caller
    # explicitly skipped (e.g. the pre-existing target they didn't
    # overwrite). don't report those as install failures here since
    # the per-target ``already exists`` line already told the story.
    probes_to_show = [
        p
        for p in probes
        # drop "missing" entries that correspond to skipped targets;
        # keep "missing" that got through an actual write attempt so
        # the anomaly is visible
        if p.status != "missing" or not (p.parent / "sponsio").exists()
    ] or probes
    all_ok = _print_skill_discovery_footer(probes_to_show)
    if not all_ok:
        # Non-zero exit so CI / "install then verify" shell scripts
        # catch drift / broken installs without having to grep output.
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _looks_like_sponsio_config(path: Path) -> bool:
    """Return True if ``path`` is probably a :file:`sponsio.yaml` (not
    an arbitrary string the user wanted to parse as a contract).

    Kept intentionally narrow so ``sponsio validate interesting.yaml`` only
    auto-routes when the file *looks* like a Sponsio config, not every YAML
    on disk.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:32768]
    except OSError:
        return False
    # Project configs list agents; ``init`` output uses version+extractor.
    if re.search(r"(?m)^\s*agents:\s*", head):
        return True
    return bool(
        re.search(r"(?m)^\s*version:\s*\d", head)
        and re.search(r"(?m)^\s*extractor:\s*", head)
    )


@cli.command()
@click.argument("contracts", nargs=-1)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True),
    help="YAML config file (sponsio.yaml)",
)
@click.option("--agent", "-a", "agent_id", help="Agent ID to validate (with --config)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option(
    "--traces",
    "trace_paths",
    multiple=True,
    type=click.Path(exists=True),
    help=(
        "Replay each parsed contract against the trace file(s) or "
        "directory.  Adds a per-contract pass/fail/error count so you "
        "can see whether a rule would have hit your historical traffic "
        "before flipping it to enforce mode.  Repeat for multiple paths."
    ),
)
def validate(contracts, config_path, agent_id, as_json, trace_paths):
    """Validate that contract strings parse into formal patterns.

    If you pass a single existing ``.yaml`` / ``.yml`` path that looks like
    a Sponsio project file (``agents:`` or ``version:`` + ``extractor:``),
    it is treated as ``--config`` automatically so ``sponsio validate
    ./sponsio.yaml`` does the right thing.

    With ``--traces``, each successfully-parsed deterministic contract is
    replayed against the supplied trace files / directories and a
    pass / fail / error count is reported alongside the parse result.
    Counts only. for per-failure attribution and repair suggestions
    see the proprietary ``sponsio-pro`` validation pipeline.

    Examples:\n
        sponsio validate "tool `A` must precede `B`"\n
        sponsio validate --config sponsio.yaml\n
        sponsio validate --config sponsio.yaml --agent customer_bot\n
        sponsio validate --config sponsio.yaml --traces traces/\n
        sponsio validate ./sponsio.yaml   # same as --config when file looks like a project config
    """
    from sponsio.generation.dsl_to_contract import (
        ContractSyntaxError,
        parse_nl_unified,
    )

    if config_path and contracts:
        click.echo(
            click.style(
                "Error: cannot use both --config and positional contracts", fg="red"
            )
        )
        sys.exit(1)

    # ``sponsio validate ./sponsio.yaml`` (forgot --config) used to try to
    # parse the *path string* as a contract. When the path exists and the
    # head of the file looks like a project config, treat it as --config.
    if not config_path and len(contracts) == 1:
        raw = contracts[0]
        p = Path(os.path.expanduser(str(raw)))
        if not p.is_absolute():
            p = Path.cwd() / p
        try:
            p = p.resolve()
        except OSError:
            p = Path(raw)
        if p.is_file() and p.suffix.lower() in (".yaml", ".yml"):
            if _looks_like_sponsio_config(p):
                if not as_json:
                    click.echo(
                        click.style("  note: ", fg="cyan", dim=True)
                        + (
                            f"treating {p} as a Sponsio config (equivalent to "
                            f"`--config {p.name}`). "
                            f"If you meant a one-line contract that looks like a path, "
                            f"quote it or use `sponsio validate --config` explicitly."
                        ),
                        err=True,
                    )
                config_path = str(p)
                contracts = ()

    if agent_id and not config_path:
        click.echo(click.style("Error: --agent requires --config", fg="red"))
        sys.exit(1)

    if not config_path and not contracts:
        click.echo("Usage: sponsio validate [CONTRACTS...] or --config FILE")
        sys.exit(1)

    # ---- trace replay setup -------------------------------------------
    # Loaded once so a 1000-contract config doesn't re-parse the trace
    # bundle 1000 times.  ``trace_paths`` is empty in the common case.
    traces_loaded: list = []
    if trace_paths:
        from sponsio.discovery.loaders import load_traces

        try:
            traces_loaded = load_traces(list(trace_paths))
        except Exception as e:  # noqa: BLE001
            click.echo(
                click.style("Error: ", fg="red")
                + f"failed to load traces from {list(trace_paths)}: {e}",
                err=True,
            )
            sys.exit(1)
        if not as_json and not traces_loaded:
            click.echo(
                click.style("  warn: ", fg="yellow")
                + "no traces loaded. replay counts will all be 0",
                err=True,
            )

    # Collect contracts to validate (flatten contract entries into
    # per-section lists for display).
    def _flatten(ac) -> dict:
        assumptions: list = []
        guarantees: list = []
        for ce in ac.contracts:
            if ce.assumption is not None:
                if isinstance(ce.assumption, list):
                    assumptions.extend(ce.assumption)
                else:
                    assumptions.append(ce.assumption)
            if ce.guarantee is not None:
                if isinstance(ce.guarantee, list):
                    guarantees.extend(ce.guarantee)
                else:
                    guarantees.append(ce.guarantee)
        return {"assumptions": assumptions, "guarantees": guarantees}

    agent_contracts: dict[str, dict] = {}

    if config_path:
        from sponsio.config import load_config

        config = load_config(config_path)
        agents_to_check = (
            {agent_id: config.agents[agent_id]} if agent_id else config.agents
        )
        for aid, ac in agents_to_check.items():
            agent_contracts[aid] = _flatten(ac)
    else:
        agent_contracts["(inline)"] = {
            "assumptions": [],
            "guarantees": list(contracts),
        }

    # Validate each contract
    all_results = []
    all_ok = True

    for aid, ag in agent_contracts.items():
        if not as_json:
            click.echo(click.style(f"\nAgent: {aid}", bold=True))

        for section, label in [
            ("assumptions", "Assumptions"),
            ("guarantees", "Guarantees"),
        ]:
            items = ag[section]
            if not items:
                continue
            if not as_json:
                click.echo(click.style(f"  {label}:", dim=True))

            for entry in items:
                # Handle both ConstraintEntry (from config) and plain strings
                from sponsio.config import ConstraintEntry, _compile_structured

                # Track the compiled formula (or DetFormula wrapper) so
                # the replay path below has a single source of truth
                # regardless of which branch produced it.
                formula_for_replay = None
                # ``result`` is only set in the NL branches; init here
                # so the replay-eligibility check below doesn't trip
                # UnboundLocalError on structured / ltl entries.
                result = None

                if isinstance(entry, ConstraintEntry):
                    if entry.is_structured:
                        try:
                            compiled = _compile_structured(entry)
                            ok = True
                            pattern = entry.pattern
                            formula = (
                                repr(compiled.formula)
                                if hasattr(compiled, "formula")
                                else ""
                            )
                            # OSS only ships deterministic patterns;
                            # ``_compile_structured`` raises on unknown
                            # names rather than falling through to sto.
                            kind = "DET"
                            nl = f"{entry.pattern}({', '.join(str(a) for a in entry.args)})"
                            formula_for_replay = compiled
                        except Exception as e:
                            ok = False
                            pattern = entry.pattern or ""
                            formula = ""
                            kind = "ERROR"
                            nl = str(e)
                    elif entry.is_ltl:
                        from sponsio.config import _compile_ltl

                        try:
                            compiled = _compile_ltl(entry)
                            ok = True
                            pattern = "ltl"
                            formula = repr(compiled.formula)
                            kind = "DET"
                            nl = entry.ltl or ""
                            formula_for_replay = compiled
                        except Exception as e:
                            ok = False
                            pattern = "ltl"
                            formula = ""
                            kind = "ERROR"
                            nl = str(e)
                    else:
                        nl = entry.nl
                        try:
                            result = parse_nl_unified(nl)
                        except ContractSyntaxError as e:
                            ok = False
                            pattern = ""
                            formula = ""
                            kind = "SYNTAX-ERROR"
                            nl = f"{entry.nl}  ({e.hint or 'no pattern matched'})"
                            result = None
                        if result is None:
                            pass  # already populated above
                        elif result.is_det:
                            ok = True
                            pattern = getattr(result.hard, "pattern_name", "")
                            formula = (
                                repr(result.hard.formula)
                                if hasattr(result.hard, "formula")
                                else ""
                            )
                            kind = "DET"
                            formula_for_replay = result.hard
                else:
                    nl = str(entry)
                    try:
                        result = parse_nl_unified(nl)
                    except ContractSyntaxError as e:
                        ok = False
                        pattern = ""
                        formula = ""
                        kind = "SYNTAX-ERROR"
                        nl = f"{str(entry)}  ({e.hint or 'no pattern matched'})"
                        result = None

                    if result is None:
                        pass  # already populated above
                    elif result.is_det:
                        ok = True
                        pattern = getattr(result.hard, "pattern_name", "")
                        formula = (
                            repr(result.hard.formula)
                            if hasattr(result.hard, "formula")
                            else ""
                        )
                        kind = "DET"
                        formula_for_replay = result.hard
                    else:
                        pattern = ""
                        formula = ""
                        kind = "UNKNOWN"
                        all_ok = False

                # Replay against historical traces \u2014 only meaningful for
                # successfully-parsed DET contracts (sto contracts need
                # an LLM judge, which sponsio-pro covers).
                replay_summary: dict | None = None
                if (
                    traces_loaded
                    and ok
                    and kind == "DET"
                    and formula_for_replay is not None
                ):
                    from sponsio.discovery.trace_replay import replay_formula

                    rep = replay_formula(formula_for_replay, traces_loaded)
                    replay_summary = {
                        "pass": rep.pass_count,
                        "fail": rep.fail_count,
                        "error": rep.error_count,
                        "pass_rate": rep.pass_rate,
                        "errors": list(rep.errors),
                    }

                entry = {
                    "nl": nl,
                    "ok": ok,
                    "type": kind.lower(),
                    "pattern": pattern,
                    "formula": formula,
                    "agent": aid,
                    "section": section,
                }
                if replay_summary is not None:
                    entry["replay"] = replay_summary
                all_results.append(entry)
                if not ok:
                    all_ok = False

                if not as_json:
                    icon = (
                        click.style("\u2713", fg="green")
                        if ok
                        else click.style("\u2717", fg="red")
                    )
                    kind_color = "cyan" if kind == "DET" else "magenta"
                    click.echo(f"    {icon} {click.style(kind, fg=kind_color)}: {nl}")
                    if pattern:
                        click.echo(click.style(f"      Pattern : {pattern}", dim=True))
                    if formula:
                        click.echo(click.style(f"      Formula : {formula}", dim=True))
                    if replay_summary is not None:
                        rate = replay_summary["pass_rate"]
                        rate_str = "n/a" if rate is None else f"{rate:.0%}"
                        replay_line = (
                            f"      Replay  : "
                            f"{replay_summary['pass']} pass / "
                            f"{replay_summary['fail']} fail"
                        )
                        if replay_summary["error"]:
                            replay_line += f" / {replay_summary['error']} error"
                        replay_line += f"  ({rate_str})"
                        # Color: green if no fails+errors, yellow if any
                        # fails / errors (the contract would block, or
                        # a trace was malformed).
                        color = (
                            "green"
                            if replay_summary["fail"] == 0
                            and replay_summary["error"] == 0
                            else "yellow"
                        )
                        click.echo(click.style(replay_line, fg=color, dim=True))

    if as_json:
        click.echo(json.dumps({"contracts": all_results, "ok": all_ok}, indent=2))
    else:
        click.echo()
        if all_ok:
            click.echo(
                click.style(
                    f"  \u2713 All {len(all_results)} contract(s) validated", fg="green"
                )
            )
        else:
            fails = sum(1 for r in all_results if not r["ok"])
            click.echo(
                click.style(f"  \u2717 {fails} contract(s) failed to parse", fg="red")
            )
        click.echo()

    # Non-zero exit on any failure so CI / pre-commit hooks catch
    # unparseable contracts instead of silently shipping them.
    if not all_ok:
        sys.exit(1)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _resolve_entry(entry):
    """Resolve a constraint entry (string or ConstraintEntry) to (nl_text, parsed_result).

    For structured entries (pattern + args), compiles directly.
    For NL strings, runs through parse_nl_unified.
    """
    from sponsio.config import ConstraintEntry, _compile_structured
    from sponsio.generation.dsl_to_contract import (
        ContractSyntaxError,
        UnifiedParseResult,
        parse_nl_unified,
    )

    if isinstance(entry, ConstraintEntry):
        if entry.is_structured:
            try:
                compiled = _compile_structured(entry)
                nl = f"{entry.pattern}({', '.join(str(a) for a in entry.args)})"
                return nl, UnifiedParseResult(original_nl=nl, hard=compiled)
            except Exception:
                return str(entry.pattern), None
        elif entry.is_ltl:
            from sponsio.config import _compile_ltl

            try:
                compiled = _compile_ltl(entry)
                return entry.ltl or "", UnifiedParseResult(
                    original_nl=entry.ltl or "", hard=compiled
                )
            except Exception:
                return entry.ltl or "ltl", None
        else:
            nl = entry.nl
    else:
        nl = str(entry)
    try:
        return nl, parse_nl_unified(nl)
    except ContractSyntaxError:
        # Unparseable. `sponsio check` signals this by returning
        # a None result, same shape as a structured-compile error.
        return nl, None


@cli.command()
@click.option(
    "--trace",
    "-t",
    "trace_path",
    required=True,
    type=click.Path(exists=True),
    help=(
        "Trace file to check against. Accepts OTLP/JSON, OTLP JSONL, "
        "native Sponsio JSON/JSONL, and session JSONL. format is "
        "sniffed from content."
    ),
)
@click.argument("contracts", nargs=-1)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True),
    help="YAML config file (sponsio.yaml)",
)
@click.option("--agent", "-a", "agent_id", help="Agent ID (with --config)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def check(trace_path, contracts, config_path, agent_id, as_json):
    """Check contracts against an OTEL trace file.

    Examples:\n
        sponsio check --trace trace.json "tool `A` must precede `B`"\n
        sponsio check --trace trace.json --config sponsio.yaml --agent bot
    """
    from sponsio.formulas.evaluator import evaluate as eval_formula
    from sponsio.tracer.grounding import ground

    if config_path and contracts:
        click.echo(
            click.style(
                "Error: cannot use both --config and positional contracts", fg="red"
            )
        )
        sys.exit(1)

    if agent_id and not config_path:
        click.echo(click.style("Error: --agent requires --config", fg="red"))
        sys.exit(1)

    if not config_path and not contracts:
        click.echo("Usage: sponsio check --trace FILE [CONTRACTS...] or --config FILE")
        sys.exit(1)

    # Load trace(s) through the unified loader so this command handles
    # the same formats as `sponsio scan --trace`.  For multi-trace
    # files (native array, native JSONL), we concatenate events into
    # one logical trace since `check` is a single-trace tool.
    from sponsio.discovery.loaders import load_trace
    from sponsio.models.trace import Trace as _Trace

    try:
        loaded = load_trace(trace_path)
    except (FileNotFoundError, IsADirectoryError, ValueError) as e:
        # Symmetric error handling with `sponsio scan -t`: any user-input
        # problem surfaces as a friendly red line rather than a traceback.
        # ``click.Path(exists=True)`` already blocks the FileNotFound case
        # for direct args, but keeping it here protects future changes
        # (e.g. accepting globs) from regressing.
        click.echo(click.style(f"Error: {e}", fg="red"))
        sys.exit(1)

    if len(loaded) == 1:
        trace = loaded[0]
    else:
        # Flatten. renumber ts so ordering is preserved across files.
        merged_events: list = []
        for t in loaded:
            for ev in t.events:
                merged_events.append(ev)
        trace = _Trace(events=merged_events)
        click.echo(
            click.style(
                f"  note: merged {len(loaded)} traces into one for evaluation",
                fg="cyan",
                dim=True,
            ),
            err=True,
        )

    if not trace.events:
        click.echo(click.style("Warning: trace is empty (no spans found)", fg="yellow"))
        sys.exit(0)

    # Collect contracts (flatten ContractEntry list for this command; per-contract
    # A->E gating is still handled in the evaluation loop below).
    assumptions: list = []
    guarantees: list = []
    check_agent = agent_id or "(inline)"

    if config_path:
        from sponsio.config import load_config

        config = load_config(config_path)
        if not agent_id:
            if len(config.agents) == 1:
                agent_id = next(iter(config.agents))
            else:
                click.echo(
                    click.style(
                        f"Error: multiple agents in config ({list(config.agents.keys())}), "
                        "use --agent to specify",
                        fg="red",
                    )
                )
                sys.exit(1)
        check_agent = agent_id
        ac = config.agents[agent_id]
        for ce in ac.contracts:
            if ce.assumption is not None:
                if isinstance(ce.assumption, list):
                    assumptions.extend(ce.assumption)
                else:
                    assumptions.append(ce.assumption)
            if ce.guarantee is not None:
                if isinstance(ce.guarantee, list):
                    guarantees.extend(ce.guarantee)
                else:
                    guarantees.append(ce.guarantee)
    else:
        guarantees = list(contracts)

    if not as_json:
        click.echo()
        click.echo(click.style(f"Checking: {check_agent}", bold=True))
        click.echo(
            click.style(f"  Trace: {trace_path} ({len(trace.events)} events)", dim=True)
        )
        click.echo()

    # Ground the trace
    valuations = ground(trace)

    # Check assumptions
    results = []
    all_pass = True

    if assumptions:
        if not as_json:
            click.echo(click.style("  Assumptions:", dim=True))
        for entry in assumptions:
            nl, parsed = _resolve_entry(entry)
            if not parsed or not parsed.is_det:
                results.append(
                    {
                        "nl": nl,
                        "section": "assume",
                        "passed": False,
                        "note": "unparseable",
                    }
                )
                all_pass = False
                if not as_json:
                    icon = click.style("\u2717", fg="red")
                    click.echo(f"    {icon} {nl}  (unparseable)")
                continue

            holds = eval_formula(parsed.hard.formula, valuations)
            results.append({"nl": nl, "section": "assume", "passed": holds})
            if not holds:
                all_pass = False
            if not as_json:
                icon = (
                    click.style("\u2713", fg="green")
                    if holds
                    else click.style("\u2717", fg="red")
                )
                verdict = (
                    click.style("pass", fg="green")
                    if holds
                    else click.style("VIOLATED", fg="red")
                )
                click.echo(f"    {icon} {nl} \u2014 {verdict}")

    # Check guarantees
    if guarantees:
        if not as_json:
            click.echo(click.style("  Guarantees:", dim=True))
        for entry in guarantees:
            nl, parsed = _resolve_entry(entry)
            if not parsed or not parsed.is_det:
                results.append(
                    {
                        "nl": nl,
                        "section": "enforce",
                        "passed": False,
                        "note": "unparseable",
                    }
                )
                all_pass = False
                if not as_json:
                    icon = click.style("\u2717", fg="red")
                    click.echo(f"    {icon} {nl}  (unparseable)")
                continue

            holds = eval_formula(parsed.hard.formula, valuations)
            results.append({"nl": nl, "section": "enforce", "passed": holds})
            if not holds:
                all_pass = False
            if not as_json:
                icon = (
                    click.style("\u2713", fg="green")
                    if holds
                    else click.style("\u2717", fg="red")
                )
                verdict = (
                    click.style("pass", fg="green")
                    if holds
                    else click.style("VIOLATED", fg="red")
                )
                click.echo(f"    {icon} {nl} \u2014 {verdict}")

    # Summary
    if as_json:
        click.echo(
            json.dumps(
                {"agent": check_agent, "results": results, "all_pass": all_pass},
                indent=2,
            )
        )
    else:
        click.echo()
        total = len([r for r in results if "note" not in r])
        passed = len([r for r in results if r["passed"] and "note" not in r])
        if all_pass:
            click.echo(
                click.style(f"  \u2713 All {total} contract(s) satisfied", fg="green")
            )
        else:
            fails = total - passed
            click.echo(
                click.style(f"  \u2717 {fails}/{total} contract(s) VIOLATED", fg="red")
            )
        click.echo()


# ---------------------------------------------------------------------------
# explain. show one contract's source + compiled form + last violation
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("query")
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="YAML config (default: ./sponsio.yaml or $SPONSIO_CONFIG).",
)
@click.option(
    "--agent",
    "agent_id",
    default=None,
    help="When the config has multiple agents, pick one.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--no-color",
    is_flag=True,
    default=False,
    help="Disable Rich color output (text mode only).",
)
def explain(
    query: str,
    config_path: str | None,
    agent_id: str | None,
    fmt: str,
    no_color: bool,
):
    """Explain a contract. source, compiled formula, last violation.

    \b
    Examples:
      sponsio explain C1                       # by alias from the session view
      sponsio explain "code freeze"            # by substring of the desc
      sponsio explain C1 --format json         # machine-readable

    The contract is resolved against the YAML config (default
    ``./sponsio.yaml`` or ``$SPONSIO_CONFIG``). Pass ``--agent`` if the
    config has multiple agents.

    Output covers what's structurally inferable from the contract +
    Sponsio's local session log:
      - the assume / enforce pattern + arguments as written
      - the compiled LTL form via ``formulas.nl_gen.formula_to_nl``
      - the most recent BLOCKED / OBSERVED event for this contract
        (scanning ``~/.sponsio/sessions/<agent>/*.jsonl``)
      - generic resolution hints based on pattern shape

    Richer overlays (LLM-driven contextual fix hints, cross-trace
    pattern stats) are an extension point not part of this build.
    """
    import os

    from sponsio.config import load_config, config_to_guard_kwargs
    from sponsio.models.agent import Agent
    from sponsio.models.contract import make_contracts
    from sponsio.render.explain import (
        explain_to_dict,
        find_last_violation,
        render_explain,
        resolve_contract,
    )

    # Resolve config path: --config > $SPONSIO_CONFIG > ./sponsio.yaml.
    cfg_path: Path | None = (
        Path(config_path)
        if config_path
        else (
            Path(os.environ["SPONSIO_CONFIG"])
            if os.environ.get("SPONSIO_CONFIG")
            else (Path("sponsio.yaml") if Path("sponsio.yaml").is_file() else None)
        )
    )
    if cfg_path is None:
        click.echo(
            click.style("Error: ", fg="red")
            + "no config found. Pass --config or create ./sponsio.yaml.",
            err=True,
        )
        raise SystemExit(2)

    try:
        config = load_config(str(cfg_path))
    except Exception as exc:
        click.echo(click.style(f"Error loading {cfg_path}: {exc}", fg="red"), err=True)
        raise SystemExit(2) from exc

    if agent_id is None:
        if len(config.agents) != 1:
            click.echo(
                click.style("Error: ", fg="red")
                + f"config has {len(config.agents)} agents. pass --agent to disambiguate "
                + f"(available: {', '.join(config.agents)})",
                err=True,
            )
            raise SystemExit(2)
        agent_id = next(iter(config.agents))
    elif agent_id not in config.agents:
        click.echo(
            click.style("Error: ", fg="red")
            + f"agent {agent_id!r} not in config (available: {', '.join(config.agents)})",
            err=True,
        )
        raise SystemExit(2)

    kw = config_to_guard_kwargs(config, agent_id)
    contracts = make_contracts(
        agent=Agent(id=agent_id), contracts=kw.get("contracts") or []
    )

    if not contracts:
        click.echo(
            click.style("Error: ", fg="red")
            + f"no contracts compiled for agent {agent_id!r}.",
            err=True,
        )
        raise SystemExit(2)

    contract, idx = resolve_contract(query, contracts)
    if contract is None:
        # Show the catalog as a hint.
        click.echo(
            click.style("Error: ", fg="red")
            + f"no contract matched {query!r}. Available:",
            err=True,
        )
        for i, c in enumerate(contracts):
            click.echo(f"  C{i + 1}  {getattr(c, 'desc', '') or '(unnamed)'}", err=True)
        raise SystemExit(2)

    last = find_last_violation(getattr(contract, "desc", "") or "")

    if fmt.lower() == "json":
        click.echo(
            json.dumps(
                explain_to_dict(contract, idx, last_violation=last),
                indent=2,
                default=str,
            )
        )
        return

    from rich.console import Console

    console = Console(
        file=sys.stderr,
        soft_wrap=True,
        highlight=False,
        color_system=None if no_color else "auto",
        force_terminal=False if no_color else None,
    )
    render_explain(
        console=console,
        contract=contract,
        index=idx,
        last_violation=last,
        config_path=cfg_path,
    )


# ---------------------------------------------------------------------------
# replay. re-render a recorded session's view from its jsonl log
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("session", required=False)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="YAML config (for the contracts-armed table; falls back to bare table).",
)
@click.option(
    "--agent",
    "agent_id_opt",
    default=None,
    help="Override the agent id derived from the session log path.",
)
@click.option(
    "--list",
    "list_only",
    is_flag=True,
    default=False,
    help="List available sessions and exit.",
)
def replay(
    session: str | None,
    config_path: str | None,
    agent_id_opt: str | None,
    list_only: bool,
):
    """Re-render a recorded session in the v1 mockup form.

    \b
    Examples:
      sponsio replay sess_4f2a            # by short ID from the session view
      sponsio replay 20260501_120000_999  # by filename stem
      sponsio replay /path/to/log.jsonl   # by direct path
      sponsio replay --list               # browse available sessions

    Reads ``~/.sponsio/sessions/<agent>/*.jsonl`` and rebuilds the
    AgentTurnSpan tree the live monitor would have produced, then
    feeds it through the same renderer the session view uses.

    Pass ``--config`` to also render the "contracts armed" table from
    the YAML. without it, only contracts mentioned in the trace are
    surfaced.
    """
    import os

    from rich.console import Console

    from sponsio.render.replay import (
        find_session_file,
        list_sessions,
        load_replay,
    )
    from sponsio.render.session_view import render_session

    console = Console(file=sys.stderr, soft_wrap=True, highlight=False)

    if list_only:
        sessions = list_sessions()
        if not sessions:
            click.echo("No sessions found in ~/.sponsio/sessions/.", err=True)
            return
        click.echo("Available sessions (most recent first):", err=True)
        for s in sessions:
            click.echo(
                f"  {s['session_id']}   agent={s['agent_id']:<24} "
                f"{s['size_bytes']:>8} bytes   {s['stem']}",
                err=True,
            )
        return

    if not session:
        click.echo(
            click.style("Error: ", fg="red")
            + "missing SESSION arg. Try `sponsio replay --list` to browse.",
            err=True,
        )
        raise SystemExit(2)

    path, agent_id = find_session_file(session)
    if path is None:
        click.echo(
            click.style("Error: ", fg="red")
            + f"no session matched {session!r}. Try `sponsio replay --list`.",
            err=True,
        )
        raise SystemExit(2)

    turn_spans, log_agent_id = load_replay(path)
    if not turn_spans:
        click.echo(
            click.style("Note: ", fg="yellow") + f"{path} has no events.",
            err=True,
        )
        return

    contracts: list = []
    final_agent_id = agent_id_opt or agent_id or log_agent_id or "(unknown)"
    cfg_path: Path | None = (
        Path(config_path)
        if config_path
        else (
            Path(os.environ["SPONSIO_CONFIG"])
            if os.environ.get("SPONSIO_CONFIG")
            else (Path("sponsio.yaml") if Path("sponsio.yaml").is_file() else None)
        )
    )
    if cfg_path is not None:
        try:
            from sponsio.config import config_to_guard_kwargs, load_config
            from sponsio.models.agent import Agent
            from sponsio.models.contract import make_contracts

            cfg = load_config(str(cfg_path))
            cfg_agent = (
                final_agent_id
                if final_agent_id in cfg.agents
                else next(iter(cfg.agents), None)
            )
            if cfg_agent:
                kw = config_to_guard_kwargs(cfg, cfg_agent)
                contracts = make_contracts(
                    agent=Agent(id=cfg_agent),
                    contracts=kw.get("contracts") or [],
                )
        except Exception as exc:
            click.echo(
                click.style("Warning: ", fg="yellow")
                + f"could not load contracts from {cfg_path}: {exc}",
                err=True,
            )

    render_session(
        console=console,
        agent_id=final_agent_id,
        mode="replay",
        contracts=contracts,
        turn_spans=turn_spans,
        session_id=session if session.startswith("sess_") else None,
    )


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--since",
    default="7d",
    show_default=True,
    help="Time window: 'all', '30m', '24h', '7d'.",
)
@click.option(
    "--agent",
    default=None,
    help="Filter to one agent_id. Default: every agent under ~/.sponsio/sessions.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(
        ["auto", "rich", "markdown", "md", "html", "json", "plain"],
        case_sensitive=False,
    ),
    default="auto",
    show_default=True,
    help=(
        "Output format. ``auto`` picks rich for an interactive terminal, "
        "markdown for piped/CI output, or plain when NO_COLOR is set."
    ),
)
@click.option(
    "--out",
    "-o",
    "out_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write report to this file. Default: stdout.",
)
@click.option(
    "--save-svg",
    "save_svg",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Save the rich-rendered output to an SVG file (vector, retina-safe).",
)
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Watch mode: re-render every --interval seconds. Ctrl+C to exit.",
)
@click.option(
    "--interval",
    default=2.0,
    show_default=True,
    type=float,
    help="Seconds between refreshes in --live mode.",
)
@click.option(
    "--base-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Override the session log directory (default: ~/.sponsio/sessions).",
)
def report(
    since: str,
    agent: str | None,
    fmt: str,
    out_path: str | None,
    save_svg: str | None,
    live: bool,
    interval: float,
    base_dir: str | None,
):
    """Summarize shadow-mode session logs into a shareable report.

    \b
    Examples:
      sponsio report                                    # rich on TTY, markdown if piped
      sponsio report --agent support_bot --since 24h    # one agent, last day
      sponsio report --format html -o report.html       # HTML to file
      sponsio report --format json --since all          # machine-readable dump
      sponsio report --save-svg report.svg              # rich + SVG export
      sponsio report --live                             # watch mode, refreshes every 2s

    Reads JSONL files written by ``mode='observe'`` (shadow mode) from
    ``~/.sponsio/sessions/<agent_id>/*.jsonl``.  Nothing is modified.
    """
    # Lazy imports so `sponsio --help` stays fast.
    from pathlib import Path

    from sponsio.render import pick_format
    from sponsio.reporting import aggregate, load_events, render
    from sponsio.reporting.reader import parse_since

    # Validate --since up front so we fail fast with a readable error.
    try:
        parse_since(since)
    except ValueError as e:
        click.echo(click.style(f"Error: {e}", fg="red"))
        raise SystemExit(2)

    bd = Path(base_dir) if base_dir else None
    resolved_fmt = pick_format(fmt)

    # SVG export requires the Rich path. promote auto/markdown to rich if asked.
    if save_svg and resolved_fmt != "rich":
        resolved_fmt = "rich"

    def _aggregate_once():
        events = load_events(since=since, agent=agent, base_dir=bd)
        return aggregate(events)

    def _render_text(report_obj) -> str:
        """Non-rich text output (markdown/html/json/plain)."""
        target = "markdown" if resolved_fmt == "plain" else resolved_fmt
        return render(report_obj, fmt=target)

    def _emit_rich(report_obj) -> None:
        """Rich path. prints directly + optionally writes SVG."""
        from sponsio.render.rich_report import render_report, save_svg as _save_svg

        console = render_report(report_obj)
        if save_svg:
            _save_svg(
                console,
                save_svg,
                title=f"Sponsio · report --since {since}",
            )
            click.echo(
                click.style("Wrote ", fg="green") + save_svg + " (SVG export)",
                err=True,
            )

    if live:
        if out_path is not None:
            click.echo(
                click.style("Error: ", fg="red")
                + "--live cannot be combined with --out."
            )
            raise SystemExit(2)
        if save_svg is not None:
            click.echo(
                click.style("Error: ", fg="red")
                + "--live cannot be combined with --save-svg."
            )
            raise SystemExit(2)
        import time as _time

        try:
            while True:
                # ANSI clear-screen + home cursor; harmless on non-TTY.
                click.echo("\x1b[2J\x1b[H", nl=False)
                report_obj = _aggregate_once()
                if resolved_fmt == "rich":
                    _emit_rich(report_obj)
                else:
                    click.echo(_render_text(report_obj))
                _time.sleep(max(0.25, interval))
        except KeyboardInterrupt:
            click.echo("\n(live mode stopped)")
            return

    report_obj = _aggregate_once()
    if resolved_fmt == "rich":
        _emit_rich(report_obj)
        if out_path is not None:
            click.echo(
                click.style("Note: ", fg="yellow")
                + "--out ignored with rich format; use --save-svg for export.",
                err=True,
            )
        return

    out = _render_text(report_obj)
    if out_path is None:
        click.echo(out, nl=False)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out)
        click.echo(
            click.style("Wrote ", fg="green")
            + out_path
            + f" ({len(out)} bytes, format={resolved_fmt})"
        )


# ---------------------------------------------------------------------------
# serve (dashboard server stub)
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", "-p", default=DASHBOARD_DEFAULT_PORT, type=int)
@click.option("--dev", is_flag=True)
def serve(host: str, port: int, dev: bool):
    """Start the Sponsio dashboard server.

    This build ships the contract runtime + CLI; the long-lived HTTP
    backend that serves the web dashboard is not part of this
    distribution. To inspect contract activity locally, use:

    \b
        sponsio host trace --follow      # live coloured stream
        sponsio report --since 1h        # session log summary
        sponsio replay <session>         # re-render a recorded session
        sponsio export-sessions --to ... # ship audit to your collector
    """
    click.echo(
        click.style("sponsio serve", bold=True)
        + ": the dashboard server is not part of this distribution "
        "(the engine ships CLI + runtime only).\n"
        "  sponsio host trace --follow  # live alternative\n"
        "  sponsio replay <session>     # re-render a recorded session view\n"
        "  sponsio report --since 1h    # session-log summary\n",
        err=True,
    )
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("paths", nargs=-1, required=True)
@click.option("--agent", "-a", default="agent", help="Agent ID for generated config")
@click.option(
    "--llm", is_flag=True, help="Enable LLM inference (auto-detects provider from env)"
)
@click.option("--model", "-m", default=None, help="LLM model (default: auto-detect)")
@click.option(
    "--provider",
    default=None,
    type=click.Choice(["openai", "anthropic", "gemini"]),
    help=(
        "LLM provider (default: auto-detect from env). "
        "Anthropic uses ANTHROPIC_API_KEY; Gemini uses GOOGLE_API_KEY "
        "or GEMINI_API_KEY (1500 req/day free tier)."
    ),
)
@click.option(
    "--base-url",
    default=None,
    help=(
        "OpenAI-compatible HTTP endpoint. Covers Ollama (local), "
        "OpenRouter, DeepSeek, Together, Groq, vLLM, Azure OpenAI. "
        "Reads OPENAI_BASE_URL env if not given."
    ),
)
@click.option(
    "--out",
    "-o",
    type=click.Path(),
    default=None,
    help=(
        "Write YAML to this path. Defaults to `./sponsio.yaml`. "
        "Use `-o -` to print to stdout for piping."
    ),
)
@click.option(
    "--append", is_flag=True, help="Append to existing file instead of overwriting"
)
@click.option(
    "--policy",
    "-p",
    multiple=True,
    type=click.Path(exists=True),
    help="Policy document (.md/.txt) to extract constraints from",
)
@click.option(
    "--push/--no-push",
    default=False,
    help=(
        "Push the YAML to the local dashboard at --push-url "
        "(default: off). The dashboard is an optional observability "
        "companion; opt in explicitly so `sponsio scan` is a pure, "
        "offline code-gen step by default."
    ),
)
@click.option(
    "--push-url",
    default="http://127.0.0.1:8000",
    help="Dashboard URL to push to (default: http://127.0.0.1:8000)",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "Read provider/model/api_key from sponsio.yaml's `extractor:` "
        "section.  Implies --llm.  Explicit --provider/--model/--base-url "
        "still win over YAML values."
    ),
)
@click.option(
    "--emit-context",
    "emit_context",
    is_flag=True,
    default=False,
    help=(
        "Skip the LLM step and instead emit the structured inputs "
        "(framework / tool inventory / scanned code excerpts / policy "
        "docs) as JSON to stdout.  Used by the host "
        "agent driving the ``sponsio`` skill: pair with "
        "``sponsio prompt scan`` and apply in the agent's own LLM "
        "context. no UnifiedExtractor call, no extra API key."
    ),
)
def scan(
    paths: tuple[str, ...],
    agent: str,
    llm: bool,
    model: str | None,
    provider: str | None,
    base_url: str | None,
    out: str | None,
    append: bool,
    policy: tuple[str, ...],
    push: bool,
    push_url: str,
    config_path: str | None,
    emit_context: bool,
):
    """Scan source code and policy docs to propose contracts.

    For first-time setup, prefer ``sponsio onboard``. it composes
    framework detection + scan + ``init``-style provider config +
    ``doctor`` health checks into a single command.  ``scan`` is the
    library-maintenance tool you reach for *after* you have a
    ``sponsio.yaml``: re-mine contracts from new code or append from
    a policy doc.

    Analyzes tool definitions, decorators, and call patterns to infer
    safety constraints. Optionally extracts constraints from policy
    documents (.md/.txt) using the discovered tool inventory as context.

    \b
    Examples:
      sponsio scan src/                                # writes ./sponsio.yaml (rule-based)
      sponsio scan src/ --llm                          # + LLM inference
      sponsio scan src/ --policy security.md --llm     # code + policy
      sponsio scan src/ -o custom.yaml                 # write to custom path
      sponsio scan src/ -o sponsio.yaml --append       # merge into existing
      sponsio scan src/ -o -                           # print to stdout (pipe)
      sponsio scan src/ --push                         # also push to dashboard
    """
    from sponsio.discovery.extractors.code_analysis import CodeAnalyzer

    # Route progress messages to stderr with light styling so the YAML
    # body on stdout is still pipeable to a file or another command.
    def _scan_progress(msg: str) -> None:
        if emit_context:
            return
        click.echo(click.style("· ", fg="cyan", dim=True) + msg, err=True)

    # ---- agent-driven path: dump inputs, skip LLM step ------------------
    # ``--emit-context`` runs the deterministic scan stages (AST tool
    # inventory, policy doc collection) and stops short of the LLM
    # contract-mining inside ``CodeAnalyzer.generate_yaml``.
    # The host agent picks up using ``sponsio prompt scan``.
    if emit_context:
        analyzer = CodeAnalyzer(use_llm=False)
        source_paths = list(paths)
        tool_inventory = analyzer.get_tool_inventory(source_paths) or []

        policy_docs: list[dict] = []
        for p in policy:
            try:
                policy_docs.append(
                    {
                        "path": str(p),
                        "content": Path(p).read_text(encoding="utf-8"),
                    }
                )
            except OSError:
                continue

        existing_yaml_text = ""
        out_path = Path(out) if out and out != "-" else Path("sponsio.yaml")
        if out_path.exists():
            try:
                existing_yaml_text = out_path.read_text(encoding="utf-8")
            except OSError:
                pass

        click.echo(
            json.dumps(
                {
                    "agent_id": agent,
                    "source_paths": source_paths,
                    "tool_inventory": tool_inventory,
                    "policy_docs": policy_docs,
                    "existing_yaml": existing_yaml_text,
                    "out_path": str(out_path),
                    "next_steps_hint": (
                        "Run ``sponsio prompt scan`` to get the prompt "
                        "template, apply it to this JSON in your own LLM "
                        f"context, then write the resulting YAML to {out_path} "
                        "via Edit/Write.  Validate with "
                        f"``sponsio validate --config {out_path}``."
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    # Pull provider/model/key/base_url from the YAML's ``extractor:``
    # section if --config was given.  CLI flags retain the highest
    # precedence. they're how you override on a one-off basis.
    api_key: str | None = None
    if config_path:
        from sponsio.config import load_config

        cfg = load_config(config_path)
        ext = cfg.extractor
        if not (ext.provider or ext.model or ext.api_key or ext.base_url):
            click.echo(
                click.style("  warn: ", fg="yellow")
                + f"{config_path} has no `extractor:` section. "
                "nothing to inherit.",
                err=True,
            )
        else:
            _scan_progress(
                f"using extractor config from {config_path} "
                f"(provider={ext.provider or '<auto>'}, "
                f"model={ext.model or '<default>'})"
            )
        provider = provider or ext.provider
        model = model or ext.model
        base_url = base_url or ext.base_url
        api_key = ext.api_key
        # --config implies --llm: configuring an extractor and then NOT
        # using it would be confusing.
        if not llm:
            llm = True
            _scan_progress("--config implies --llm; enabling LLM inference")

    analyzer = CodeAnalyzer(
        use_llm=llm,
        llm_model=model,
        api_key=api_key,
        provider=provider,
        base_url=base_url,
        progress=_scan_progress,
    )
    source_paths = list(paths)

    # Extract tool inventory for policy document context
    tool_inventory = analyzer.get_tool_inventory(source_paths) if policy else None

    yaml_content = analyzer.generate_yaml(
        source_paths,
        agent_id=agent,
        policy_paths=list(policy),
        tool_inventory=tool_inventory,
    )

    # --- Auto-validate & drop unparseable contracts ---------------------
    # Goal: the file we hand the user is *directly usable*. Any contract
    # that the parser can't compile is dropped here (and listed on
    # stderr) instead of being left as a landmine in the YAML.
    yaml_content, dropped_contracts = _filter_invalid_contracts(yaml_content)

    # --- Post-scan summary (stderr) -------------------------------------
    # Helps users notice the "0 contracts" case immediately and points
    # them at --llm if they only ran the AST pass.
    n_tools, n_contracts, n_review = _scan_summary_counts(yaml_content)
    summary_color = "green" if n_contracts > 0 else "yellow"
    summary = f"Scan summary: {n_tools} tool(s), {n_contracts} contract(s) kept"
    if n_review:
        summary += f" ({n_review} flagged for review)"
    if dropped_contracts:
        summary += f", {len(dropped_contracts)} dropped (failed to parse)"
    click.echo(click.style("• " + summary, fg=summary_color), err=True)
    for d in dropped_contracts:
        click.echo(
            click.style("  dropped: ", fg="yellow")
            + f"[{d['agent']}] "
            + click.style(d["nl"][:120], dim=True)
            + (f"  ({d['error']})" if d.get("error") else ""),
            err=True,
        )
    if n_tools == 0:
        click.echo(
            click.style("  note: ", fg="cyan")
            + "0 tools usually means nothing in the scanned path matched "
            "Sponsio's discovery rules (``@tool``, ``Agent(tools=[...])``, "
            "``TOOLS = [fn, ...]``, etc.), or the tree was effectively empty "
            "(dependency dirs like ``.venv`` / ``node_modules`` are skipped). "
            "Point at the directory that contains your agent's tool modules.",
            err=True,
        )
    if n_contracts == 0 and not llm:
        click.echo(
            click.style("  hint: ", fg="cyan")
            + "no contracts inferred from AST. Re-run with "
            + click.style("--llm", bold=True)
            + " (and optionally --policy <doc>) for richer inference.",
            err=True,
        )
    if policy and not llm:
        click.echo(
            click.style("  warn: ", fg="yellow")
            + "--policy was given but --llm was not. "
            + f"{len(policy)} policy file(s) were ignored.",
            err=True,
        )

    # Default output: write to ``./sponsio.yaml`` so the common
    # interactive case never leaves the user wondering where the YAML
    # went.  Two opt-outs:
    #   * ``-o -``      → print to stdout (pipeline use)
    #   * ``-o <path>`` → write to a specific path
    if out == "-":
        click.echo(yaml_content)
        click.echo(
            click.style("• ", fg="cyan")
            + "YAML written to stdout (use `-o <path>` to save to a file).",
            err=True,
        )
    else:
        target = out or "sponsio.yaml"
        existed = os.path.exists(target)
        if append and existed:
            with open(target) as f:
                existing = f.read()
            yaml_content = _merge_yaml(existing, yaml_content)
        with open(target, "w") as f:
            f.write(yaml_content)
        abs_out = os.path.abspath(target)
        verb = (
            "Updated" if append and existed else ("Overwrote" if existed else "Wrote")
        )
        click.echo(
            click.style("✓ ", fg="green") + f"{verb} {click.style(abs_out, bold=True)}",
            err=True,
        )
        if existed and not append:
            click.echo(
                click.style("  note: ", fg="yellow")
                + "existing file was overwritten. "
                + "Use --append to merge new contracts into it instead.",
                err=True,
            )
        click.echo(
            click.style("  tip: ", fg="cyan", dim=True)
            + f"re-run `sponsio validate --config {abs_out}` after manual edits.",
            err=True,
        )

    if push:
        _push_scan_to_dashboard(
            yaml_content=yaml_content,
            filename=(os.path.basename(out) if out and out != "-" else "sponsio.yaml"),
            dashboard_url=push_url,
            source_paths=source_paths,
        )


def _filter_invalid_contracts(yaml_content: str) -> tuple[str, list[dict]]:
    """Drop contracts that fail to compile so the saved YAML is usable as-is.

    Walks every ``agents.<id>.contracts[*]`` entry, runs the same parser
    that ``sponsio validate`` uses, and rewrites the YAML with only the
    entries that parse cleanly. Bad ones are returned for stderr display.

    Conservative on errors: if PyYAML / the parser modules aren't
    importable, returns the input unchanged (and an empty drop list) so
    a minimal install still gets a working scan, just without the
    auto-validate net.

    Returns:
        (cleaned_yaml, dropped) where ``dropped`` is a list of
        ``{"agent": str, "nl": str, "error": str}``.
    """
    try:
        import yaml as _yaml
    except ImportError:
        return yaml_content, []

    try:
        from sponsio.config import (
            _compile_ltl,
            _compile_structured,
            _parse_constraint_entry,
        )
        from sponsio.generation.dsl_to_contract import (
            ContractSyntaxError,
            parse_nl_unified,
        )
    except ImportError:
        return yaml_content, []

    try:
        data = _yaml.safe_load(yaml_content)
    except _yaml.YAMLError:
        return yaml_content, []

    if not isinstance(data, dict):
        return yaml_content, []

    agents_raw = data.get("agents", {})
    if not isinstance(agents_raw, dict):
        return yaml_content, []

    def _validate_one(item) -> tuple[bool, str, str]:
        try:
            entry = _parse_constraint_entry(item)
        except Exception as e:  # noqa: BLE001
            return False, str(item)[:120], f"parse: {e}"
        if entry.is_structured:
            try:
                _compile_structured(entry)
            except Exception as e:  # noqa: BLE001
                args = ", ".join(str(a) for a in (entry.args or []))
                return False, f"{entry.pattern}({args})", str(e)
            return True, "", ""
        elif entry.is_ltl:
            try:
                _compile_ltl(entry)
            except Exception as e:  # noqa: BLE001
                return False, (entry.ltl or "")[:120], str(e)
            return True, "", ""
        else:
            nl = entry.nl or ""
            try:
                parse_nl_unified(nl)
            except ContractSyntaxError as e:
                return False, nl, e.hint or "no pattern matched"
            except Exception as e:  # noqa: BLE001
                return False, nl, str(e)
            return True, "", ""

    bad_per_agent: dict[str, set[int]] = {}
    dropped: list[dict] = []

    for agent_id, ag in agents_raw.items():
        # An agent block is normally a dict with `contracts:`; bare lists
        # are tolerated by the loader but rare from generate_yaml. Handle
        # both for safety.
        if isinstance(ag, dict):
            contracts = ag.get("contracts", [])
        elif isinstance(ag, list):
            contracts = ag
        else:
            continue
        if not isinstance(contracts, list):
            continue

        bad: set[int] = set()
        for idx, ce in enumerate(contracts):
            # An entry can be either a bare string (E only, NL form), or
            # a dict with A/G keys whose values are themselves NL strings
            # or structured ``{pattern, args}`` dicts.
            sub_items: list = []
            if isinstance(ce, str):
                sub_items.append(ce)
            elif isinstance(ce, dict):
                for key in ("A", "G"):
                    if key not in ce:
                        continue
                    val = ce[key]
                    sub_items.extend(val if isinstance(val, list) else [val])
            else:
                continue

            entry_dropped = False
            for it in sub_items:
                if it is None:
                    continue
                ok, nl_repr, err = _validate_one(it)
                if not ok:
                    dropped.append(
                        {"agent": str(agent_id), "nl": nl_repr, "error": err}
                    )
                    entry_dropped = True
                    break
            if entry_dropped:
                bad.add(idx)

        if bad:
            bad_per_agent[str(agent_id)] = bad

    if not bad_per_agent:
        return yaml_content, dropped  # nothing to rewrite

    cleaned = _drop_contract_indices(yaml_content, bad_per_agent)
    return cleaned, dropped


def _drop_contract_indices(
    yaml_content: str, bad_per_agent: dict[str, set[int]]
) -> str:
    """Remove specific contract entries (by 0-based index) per agent.

    Preserves comments, confidence tags and the surrounding YAML
    structure that ``generate_yaml`` produces.  If an agent's
    ``contracts:`` list ends up empty we replace it with ``contracts: []``
    so the resulting file still parses.
    """
    out: list[str] = []
    lines = yaml_content.split("\n")

    in_agents = False
    current_agent: str | None = None
    in_contracts = False
    current_idx = -1
    skipping = False
    contracts_line_idx: int | None = None
    kept_in_current_contracts = 0

    def _finalize_contracts_block() -> None:
        # If the contracts: list ended up empty, swap the header line for
        # ``contracts: []`` so the YAML stays valid.
        nonlocal contracts_line_idx, kept_in_current_contracts
        if contracts_line_idx is not None and kept_in_current_contracts == 0:
            header = out[contracts_line_idx]
            stripped = header.lstrip()
            indent = header[: len(header) - len(stripped)]
            if stripped.rstrip().endswith(":"):
                out[contracts_line_idx] = f"{indent}contracts: []"
        contracts_line_idx = None
        kept_in_current_contracts = 0

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Blank / comment lines: keep unless we're inside a dropped entry.
        if not stripped or stripped.startswith("#"):
            if skipping:
                continue
            out.append(line)
            continue

        # Top-level key (col 0) → reset everything.
        if indent == 0:
            _finalize_contracts_block()
            in_agents = stripped.startswith("agents:")
            current_agent = None
            in_contracts = False
            current_idx = -1
            skipping = False
            out.append(line)
            continue

        # Inside agents: each agent header sits at indent 2.
        if in_agents and indent == 2 and stripped.rstrip().endswith(":"):
            _finalize_contracts_block()
            current_agent = stripped.rstrip()[:-1].strip()
            in_contracts = False
            current_idx = -1
            skipping = False
            out.append(line)
            continue

        # Properties of the current agent live at indent 4.
        if current_agent is not None and indent == 4:
            _finalize_contracts_block()
            in_contracts = stripped.startswith("contracts:")
            skipping = False
            current_idx = -1
            if in_contracts:
                contracts_line_idx = len(out)
                kept_in_current_contracts = 0
            out.append(line)
            continue

        # Inside a contracts: list, entries start at indent 6 with "- ".
        if in_contracts and indent >= 6:
            if stripped.startswith("- "):
                current_idx += 1
                bad_set = bad_per_agent.get(current_agent or "", set())
                skipping = current_idx in bad_set
                if not skipping:
                    kept_in_current_contracts += 1
                    out.append(line)
                continue
            # Continuation line of the current entry.
            if not skipping:
                out.append(line)
            continue

        # Anything else: outside our tracked regions.
        skipping = False
        out.append(line)

    _finalize_contracts_block()
    return "\n".join(out)


def _scan_summary_counts(yaml_content: str) -> tuple[int, int, int]:
    """Count tools, contracts and review-flagged contracts in scan YAML.

    Tolerant to formatting; we just look for stable line shapes that the
    YAML emitter produces.  Returns ``(tools, contracts, review_flagged)``.
    """
    n_tools = 0
    n_contracts = 0
    n_review = 0
    in_tools = False
    for raw in yaml_content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if line.startswith("tools:"):
            in_tools = True
            continue
        if in_tools:
            if line.startswith("  - name:"):
                n_tools += 1
                continue
            if line and not line.startswith(" "):
                in_tools = False
        if stripped.startswith("- G:") or stripped.startswith("- A:"):
            n_contracts += 1
            if "review recommended" in stripped:
                n_review += 1
    return n_tools, n_contracts, n_review


def _push_scan_to_dashboard(
    yaml_content: str,
    filename: str,
    dashboard_url: str,
    source_paths: list[str],
) -> None:
    """POST the scan YAML to the running dashboard.

    Silently skips if the dashboard isn't reachable; this is additive UX,
    not a required step.
    """
    base = dashboard_url.rstrip("/")
    try:
        import httpx
    except ImportError:
        click.echo(
            click.style("  note: ", fg="yellow")
            + "httpx not installed, skipping dashboard push."
        )
        return

    # 1. Check that the dashboard is actually running before uploading.
    try:
        r = httpx.get(f"{base}/api/health", timeout=1.5)
        if r.status_code != 200:
            raise RuntimeError(f"/api/health returned {r.status_code}")
    except Exception:
        click.echo(
            click.style("  tip: ", fg="cyan")
            + f"dashboard not running at {base}. start it with "
            + click.style("sponsio serve", bold=True)
            + " to see scan results in the UI."
        )
        return

    # 2. POST the YAML as a file upload, tagged with source=cli so the
    #    dashboard's CLI tab can distinguish it from browser uploads.
    try:
        files = {"file": (filename, yaml_content.encode("utf-8"), "text/yaml")}
        r = httpx.post(
            f"{base}/api/scan/upload",
            files=files,
            params={"source": "cli"},
            timeout=10.0,
        )
        if r.status_code != 200:
            click.echo(
                click.style("  push failed: ", fg="yellow")
                + f"HTTP {r.status_code} {r.text[:200]}"
            )
            return
        result = r.json()
        summary = (
            f"{result.get('agent_name', '?')}: "
            f"{result.get('score', 0)}/100 "
            f"({result.get('grade', '?')})"
        )
        click.echo(click.style("✓ ", fg="green") + f"Pushed to dashboard. {summary}")
        click.echo(
            f"  View at {click.style(base.replace(':8000', ':3000') + '/scan', bold=True)}"
        )
    except Exception as e:
        click.echo(click.style("  push failed: ", fg="yellow") + str(e))


def _merge_yaml(existing: str, new: str) -> str:
    """Merge new scan results into an existing YAML file.

    Appends new contract entries (``- E:`` / ``- A: ... E:``) from
    *new* after the last contract in *existing*, avoiding duplicates.

    Works with the current ``contracts: [{A, E}]`` YAML schema.
    """
    existing_lines = existing.rstrip().split("\n")

    # --- Extract contract entries from new content ---
    # A contract entry starts with a line matching `- E:` or `- A:` at
    # the expected indent (6 spaces inside `contracts:`). Continuation
    # lines are indented deeper.
    new_lines = new.split("\n")
    new_entries: list[list[str]] = []
    in_contracts = False
    current_entry: list[str] = []

    for line in new_lines:
        stripped = line.strip()
        if "contracts:" in line and stripped != "contracts: []":
            in_contracts = True
            continue
        if not in_contracts:
            continue
        # A new entry starts with `- E:` or `- A:` (possibly with trailing comment)
        if stripped.startswith("- G:") or stripped.startswith("- A:"):
            if current_entry:
                new_entries.append(current_entry)
            current_entry = [line]
        elif current_entry and (
            stripped.startswith("pattern:")
            or stripped.startswith("args:")
            or stripped.startswith("source:")
            or stripped.startswith("G:")
            or stripped.startswith("desc:")
        ):
            # Continuation of the current entry
            current_entry.append(line)
        elif current_entry and not stripped and not stripped.startswith("#"):
            # Blank line or end of section
            pass
        elif current_entry and stripped.startswith("#"):
            # Comment inside an entry. keep it
            current_entry.append(line)
        elif not stripped:
            continue
        else:
            # Non-entry, non-continuation line. we've left the contracts block
            break
    if current_entry:
        new_entries.append(current_entry)

    if not new_entries:
        return existing

    # --- Fingerprint existing entries to deduplicate ---
    # Normalize each entry to a single key string for comparison.
    def _fingerprint(lines: list[str]) -> str:
        return " ".join(ln.strip() for ln in lines)

    existing_fingerprints: set[str] = set()
    temp_entry: list[str] = []
    in_existing_contracts = False
    for line in existing_lines:
        stripped = line.strip()
        if "contracts:" in line:
            in_existing_contracts = True
            continue
        if not in_existing_contracts:
            continue
        if stripped.startswith("- G:") or stripped.startswith("- A:"):
            if temp_entry:
                existing_fingerprints.add(_fingerprint(temp_entry))
            temp_entry = [line]
        elif temp_entry and stripped and not stripped.startswith("#"):
            temp_entry.append(line)
        elif not stripped:
            continue
    if temp_entry:
        existing_fingerprints.add(_fingerprint(temp_entry))

    # Filter out duplicates
    to_add = [
        entry
        for entry in new_entries
        if _fingerprint(entry) not in existing_fingerprints
    ]

    if not to_add:
        return existing

    # --- Append after last content line ---
    result = existing.rstrip() + "\n"
    result += "      # --- appended by sponsio scan ---\n"
    for entry in to_add:
        result += "\n".join(entry) + "\n"
    return result


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@cli.command(name="export")
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=True, dir_okay=True, path_type=Path),
)
@click.option(
    "--to",
    "target_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Output directory for OTLP-JSON trace files.",
)
@click.option(
    "--label",
    type=click.Choice(["safe", "unsafe", "none"]),
    default="safe",
    show_default=True,
    help=(
        "Filename prefix applied to each output trace.  ``safe`` / "
        "``unsafe`` make the file ready for `sponsio eval`; ``none`` "
        "preserves the input basename untouched (useful when you've "
        "already pre-labelled Sponsio-native dumps)."
    ),
)
@click.option(
    "--agent",
    "agent_id",
    default=None,
    help=(
        "Override the ``service.name`` stamped on the OTLP output.  "
        "Defaults to the ``metadata.agent_id`` in the source JSON, "
        "then to the first event's ``agent``, then to ``'agent'``."
    ),
)
@click.option(
    "--glob",
    "glob_pattern",
    default="*.json",
    show_default=True,
    help="Only convert files matching this glob (directory mode only).",
)
def export_cmd(
    source: Path,
    target_dir: Path,
    label: str,
    agent_id: str | None,
    glob_pattern: str,
):
    """Convert Sponsio-native trace dumps to OTLP JSON for ``sponsio eval``.

    The canonical flow from prod to eval corpus:

    \b
        # 1. In your agent (observe mode. never blocks):
        guard = BaseGuard(agent_id="bot", contracts=[...], mode="observe")
        # ...runs happen, violations logged but not enforced...

        # 2. Dump the accumulated trace to disk at session end:
        guard.trace.export("/var/log/sponsio/run.json")

        # 3. Later, convert a directory of these dumps into an eval corpus:
        sponsio export /var/log/sponsio/ --to traces/ --label safe

        # 4. Re-label incident traces and re-run eval:
        mv traces/safe_run_123.json traces/unsafe_run_123.json
        sponsio eval traces/ --config sponsio.yaml

    SOURCE may be a single ``.json`` file or a directory of them.
    Output filenames are ``{label}_{source-basename}.json``. the
    prefix is what ``sponsio eval`` reads to know which traces are
    expected to pass vs be blocked, so picking the right ``--label``
    at export time saves a rename pass later.
    """
    from sponsio.models.trace import Trace
    from sponsio.tracer.otel_writer import trace_to_otlp

    # Collect source files
    if source.is_file():
        sources = [source]
    else:
        sources = sorted(source.glob(glob_pattern))
        if not sources:
            click.echo(
                click.style(
                    f"No files matched {glob_pattern} under {source}", fg="yellow"
                ),
                err=True,
            )
            sys.exit(0)

    target_dir.mkdir(parents=True, exist_ok=True)

    converted = 0
    skipped: list[tuple[Path, str]] = []

    for src in sources:
        try:
            raw = json.loads(src.read_text())
        except (json.JSONDecodeError, OSError) as e:
            skipped.append((src, f"read: {e}"))
            continue

        # Accept either the bare Trace dict shape ({"events": [...], "metadata": {...}})
        # OR the richer ``export_trace()`` envelope (same shape, extra metadata).
        # Reject OTLP input. that's already in the target shape and would
        # silently duplicate rather than convert.
        if "resourceSpans" in raw:
            skipped.append((src, "already OTLP JSON. refusing to re-wrap"))
            continue
        if "events" not in raw:
            skipped.append((src, "no 'events' key. not a Sponsio trace dump"))
            continue

        try:
            trace = Trace.from_dict(raw)
        except (KeyError, TypeError) as e:
            skipped.append((src, f"parse: {e}"))
            continue

        effective_agent = (
            agent_id or (raw.get("metadata") or {}).get("agent_id") or None
        )
        otlp = trace_to_otlp(trace, agent_id=effective_agent)

        # Figure out output filename + label prefix
        stem = src.stem
        if label == "none":
            out_name = f"{stem}.json"
        else:
            # Don't double-prefix if the source already has safe_/unsafe_
            lowered = stem.lower()
            if lowered.startswith(("safe_", "safe-", "unsafe_", "unsafe-")):
                out_name = f"{stem}.json"
            else:
                out_name = f"{label}_{stem}.json"

        out_path = target_dir / out_name
        out_path.write_text(json.dumps(otlp, indent=2))
        converted += 1

    click.echo(
        click.style("✓ ", fg="green")
        + f"Converted {converted} trace(s) to {target_dir}"
    )
    if skipped:
        click.echo(click.style("  skipped:", fg="yellow"))
        for p, why in skipped:
            click.echo(f"    · {p.name}. {why}")


# ---------------------------------------------------------------------------
# `sponsio export-sessions`. push session audit log to OTLP
# ---------------------------------------------------------------------------


def _parse_since(since: str) -> float:
    """Parse a relative duration like ``"24h"`` / ``"7d"`` / ``"30m"``
    into a Unix-timestamp cutoff (seconds).

    Returns ``0.0`` (= no cutoff) for the empty / sentinel values the
    user might pass when they want everything. Bare integers are
    interpreted as hours (``--since 6`` == ``--since 6h``) since
    ``hour`` is the unit operators reach for first.
    """
    import re as _re

    s = (since or "").strip().lower()
    if not s or s in ("0", "all"):
        return 0.0
    m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", s)
    if not m:
        raise click.BadParameter(
            f"invalid --since value {since!r}; expected '24h' / '7d' / '30m' / '90s'",
        )
    n = float(m.group(1))
    unit = m.group(2) or "h"
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return time.time() - n * multipliers[unit]


def _session_event_to_otlp_span(event: dict) -> dict:
    """Convert one ``MonitorEvent``-shaped JSONL record into an OTLP span.

    The session log captures *flat* monitor events (one row per
    contract verdict), not the full span tree. We synthesise a
    self-contained OTLP span per event so the dashboard's "Today's
    blocks" card has the same attribute keys it gets from live
    span-tree exports.

    Lossy on purpose: we don't re-derive the contract_check tree from
    flat events, so the violation card works but the rule-fire-heatmap
    won't have per-phase precondition / guarantee detail. That's
    acceptable for historical replay; live exports keep the full tree.
    """
    from sponsio.tracer import semconv

    ts_unix = float(event.get("ts") or 0.0)
    ts_ns = int(ts_unix * 1_000_000_000) if ts_unix else 0
    result = event.get("result") or {}
    action = result.get("action") or "allowed"
    blocked = action in ("blocked", "escalated", "observed")

    attrs: list[dict] = []
    if event.get("agent_id"):
        attrs.append(_attr_for_session(semconv.ATTR_AGENT_ID, event["agent_id"]))
    if event.get("action"):
        attrs.append(_attr_for_session(semconv.ATTR_EVENT_TOOL, event["action"]))
    if ts_ns:
        attrs.append(_attr_for_session(semconv.ATTR_EVENT_TIMESTAMP_NS, ts_ns))
    if event.get("pipeline"):
        # ``hard`` is the legacy alias; emit the public ``det`` name.
        pipeline = "det" if event["pipeline"] == "hard" else event["pipeline"]
        attrs.append(_attr_for_session(semconv.ATTR_CONTRACT_PIPELINE, pipeline))
    if event.get("constraint"):
        attrs.append(
            _attr_for_session(semconv.ATTR_CONTRACT_LABEL, event["constraint"])
        )
    attrs.append(_attr_for_session(semconv.ATTR_OUTCOME_BLOCKED, bool(blocked)))
    attrs.append(
        _attr_for_session(
            semconv.ATTR_OUTCOME_STATUS,
            "violated" if blocked else "ok",
        )
    )
    attrs.append(_attr_for_session(semconv.ATTR_ENFORCEMENT_ACTION, action))
    if result.get("message"):
        attrs.append(
            _attr_for_session(semconv.ATTR_VIOLATION_EVIDENCE, result["message"])
        )

    return {
        "traceId": "0" * 32,
        "spanId": f"{int(ts_unix * 1000):016x}" if ts_ns else "0" * 16,
        "name": semconv.SPAN_AGENT_TURN,
        "startTimeUnixNano": str(ts_ns or 0),
        "endTimeUnixNano": str(ts_ns or 0),
        "status": {"code": 2 if blocked else 1},
        "attributes": attrs,
    }


def _attr_for_session(key: str, value):
    """Local copy of otel_writer._attr. used by the session importer
    so we don't leak the writer's private API into this CLI command."""
    if isinstance(value, bool):
        v: dict = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, float):
        v = {"doubleValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


@cli.command(name="export-sessions")
@click.option(
    "--since",
    default="24h",
    show_default=True,
    help=(
        "Time window relative to now: ``24h`` / ``7d`` / ``30m`` / "
        "``90s``, or ``all`` for no cutoff. Bare numbers default to "
        "hours."
    ),
)
@click.option(
    "--agent",
    "agent_filter",
    default=None,
    help=(
        "Only export sessions for this agent_id. Defaults to all "
        "agents under ``~/.sponsio/sessions/``."
    ),
)
@click.option(
    "--sessions-dir",
    "sessions_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help=(
        "Override the source directory. Default: "
        "``$SPONSIO_SESSIONS_DIR`` or ``~/.sponsio/sessions/``."
    ),
)
@click.option(
    "--to",
    "destination",
    required=True,
    help=(
        "Output destination. Either an OTLP file path "
        "(``./traces.jsonl``) or an HTTP endpoint "
        "(``https://collector.example.com/v1/traces``)."
    ),
)
@click.option(
    "--header",
    "headers_raw",
    multiple=True,
    help=(
        "Extra HTTP headers as ``Key: Value``. May be specified "
        "multiple times. Auth keys, tenant ids etc. go here. Only "
        "honored when ``--to`` is an HTTP URL."
    ),
)
@click.option(
    "--batch-size",
    type=int,
    default=50,
    show_default=True,
    help="Spans per HTTP POST (HTTP destination only).",
)
@click.option(
    "--service-name",
    default=None,
    help=(
        "OTLP ``resource.service.name`` stamped on every exported "
        "span. Defaults to the per-agent_id of each session file."
    ),
)
def export_sessions_cmd(
    since: str,
    agent_filter: str | None,
    sessions_dir: Path | None,
    destination: str,
    headers_raw: tuple[str, ...],
    batch_size: int,
    service_name: str | None,
):
    """Ship audit-log session events to an OTLP destination.

    Reads ``~/.sponsio/sessions/<agent_id>/*.jsonl``, converts each
    ``MonitorEvent`` row into an OTLP span using the Sponsio Semantic
    Conventions (see ``docs/reference/observability.md``), and writes them
    either to a local OTLP-JSONL file or POSTs them to an OTLP/HTTP
    collector (Datadog, Honeycomb, Grafana Cloud, the Sponsio-native
    dashboard, …).

    \b
    Examples:
      # Last 24h of audit, all agents, push to your dashboard
      sponsio export-sessions --to https://obs.example.com/v1/traces \\
                              --header "x-api-key: $OBS_API_KEY"

      # Last 7d of one agent, write to a file
      sponsio export-sessions --since 7d --agent _host_cursor \\
                              --to ./audit-export.jsonl

      # Everything we have, no time cutoff
      sponsio export-sessions --since all --to ./full-audit.jsonl

    The session log is the audit substrate (``MonitorEvent``-flat
    records); the runtime span tree (per-phase precondition /
    guarantee / sto_eval children) is *not* persisted to disk, so
    historical exports are necessarily lossy on per-phase detail.
    Live exports via :class:`sponsio.tracer.exporters.OtlpHttpExporter`
    carry the full tree.
    """
    from sponsio.runtime.session_log import _resolve_default_base_dir

    cutoff = _parse_since(since)
    base = (
        sessions_dir.expanduser()
        if sessions_dir is not None
        else _resolve_default_base_dir()
    )

    if not base.exists():
        click.echo(
            click.style(f"sessions dir not found: {base}", fg="yellow"),
            err=True,
        )
        sys.exit(0)

    # Walk per-agent subdirectories.
    agent_dirs: list[Path]
    if agent_filter is not None:
        agent_dirs = [base / agent_filter]
        if not agent_dirs[0].is_dir():
            click.echo(
                click.style(f"no sessions for agent {agent_filter!r}", fg="yellow"),
                err=True,
            )
            sys.exit(0)
    else:
        agent_dirs = [p for p in base.iterdir() if p.is_dir()]

    spans: list[dict] = []
    by_agent: dict[str, int] = {}

    for agent_dir in sorted(agent_dirs):
        agent_id = agent_dir.name
        for jsonl_path in sorted(agent_dir.glob("*.jsonl")):
            try:
                lines = jsonl_path.read_text().splitlines()
            except OSError as e:
                click.echo(
                    click.style(f"  skip {jsonl_path}: {e}", fg="yellow"), err=True
                )
                continue
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if cutoff and float(rec.get("ts") or 0.0) < cutoff:
                    continue
                spans.append(_session_event_to_otlp_span(rec))
                by_agent[agent_id] = by_agent.get(agent_id, 0) + 1

    if not spans:
        click.echo(
            click.style(
                f"no events matched (since={since}, agent={agent_filter})",
                fg="yellow",
            ),
            err=True,
        )
        sys.exit(0)

    # Emit one OTLP envelope.
    from sponsio.tracer import semconv as _semconv

    envelope = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr_for_session(
                            "service.name",
                            service_name or "sponsio-sessions",
                        ),
                    ],
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "sponsio",
                            "version": _semconv.SCHEMA_VERSION,
                        },
                        "schemaUrl": _semconv.SCHEMA_URL,
                        "spans": spans,
                    }
                ],
            }
        ],
    }

    if destination.startswith(("http://", "https://")):
        # HTTP push via the in-tree batching exporter.
        headers: dict[str, str] = {}
        for raw in headers_raw:
            if ":" not in raw:
                raise click.BadParameter(f"--header must be 'Key: Value' (got {raw!r})")
            k, _, v = raw.partition(":")
            headers[k.strip()] = v.strip()

        body = json.dumps(envelope).encode("utf-8")
        click.echo(
            f"POSTing {len(spans)} spans ({len(body) / 1024:.1f} KB) → {destination}"
        )
        try:
            req = urllib.request.Request(
                destination,
                data=body,
                headers={"Content-Type": "application/json", **headers},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if not (200 <= resp.status < 300):
                    click.echo(
                        click.style(
                            f"collector returned HTTP {resp.status}",
                            fg="red",
                        ),
                        err=True,
                    )
                    sys.exit(1)
        except urllib.error.URLError as e:
            click.echo(click.style(f"HTTP push failed: {e}", fg="red"), err=True)
            sys.exit(1)
        click.secho(f"✓ pushed {len(spans)} spans", fg="green")
    else:
        # File destination. write the OTLP envelope as a single JSON.
        out = Path(destination).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(envelope, indent=2))
        click.secho(
            f"✓ wrote {len(spans)} spans → {out} ({out.stat().st_size / 1024:.1f} KB)",
            fg="green",
        )

    # Summary by agent. useful when --agent isn't set.
    if by_agent:
        click.echo()
        click.echo(click.style("By agent:", bold=True))
        for agent_id, n in sorted(by_agent.items(), key=lambda x: -x[1]):
            click.echo(f"  {agent_id:30}  {n:6} events")

    click.echo()
    click.echo(
        click.style("Schema: ", dim=True)
        + f"{_semconv.SCHEMA_URL} (version {_semconv.SCHEMA_VERSION})"
    )


@cli.command(name="eval")
@click.argument(
    "trace_path",
    type=click.Path(exists=True, file_okay=True, dir_okay=True, path_type=Path),
)
@click.argument("contracts", nargs=-1)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True),
    help="YAML config file (sponsio.yaml)",
)
@click.option("--agent", "-a", "agent_id", help="Agent ID (with --config)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Diff against a previous JSON report (produced by `--json`).  "
        "Surfaces FPR/FNR deltas per contract and overall."
    ),
)
@click.option(
    "--max-fpr-delta",
    type=float,
    default=None,
    help=(
        "Fail (exit 1) if overall FPR rose by more than this many "
        "percentage points vs --baseline.  E.g. `0.01` = 1pp.  "
        "Use in CI to catch overblock regressions automatically."
    ),
)
@click.option(
    "--max-fnr-delta",
    type=float,
    default=None,
    help=(
        "Fail (exit 1) if overall FNR rose by more than this many "
        "percentage points vs --baseline.  Use to catch regressions "
        "where contracts started missing real incidents."
    ),
)
@click.option(
    "--write-baseline",
    "write_baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "After running, write the report JSON to this path.  Use to "
        "snapshot a green run as the new baseline for the next PR."
    ),
)
def eval_cmd(
    trace_path: Path,
    contracts,
    config_path,
    agent_id,
    as_json,
    baseline_path: Path | None,
    max_fpr_delta: float | None,
    max_fnr_delta: float | None,
    write_baseline_path: Path | None,
):
    """Replay a labelled trace corpus and report FPR/FNR per contract.

    Use this BEFORE flipping ``SPONSIO_MODE=enforce``. it answers
    "if I turn enforcement on tomorrow, how often will my contracts
    over-block legitimate traffic, and how often will they miss real
    incidents?".

    Label convention: filename prefix.\n
    \b
        safe_login.json     → expected to PASS every contract
        unsafe_drop.json    → expected to be BLOCKED by ≥1 contract
        anything_else.json  → counted but not used in FPR/FNR

    Examples:\n
        sponsio eval traces/ --config sponsio.yaml --agent bot\n
        sponsio eval traces/ "tool `transfer` at most 1 times"\n
        sponsio eval traces/ --config sponsio.yaml --json\n
        sponsio eval traces/ -c sponsio.yaml \\\n
            --baseline main-baseline.json --max-fpr-delta 0.01

    Reasonable CI gates: ``--max-fpr-delta 0.01`` (1pp overblock
    regression budget) and ``--max-fnr-delta 0.0`` (zero tolerance
    for new misses).  Adjust to your appetite.
    """
    from sponsio.eval_runner import (
        diff_reports,
        discover_cases,
        format_diff,
        format_report,
        run_eval,
    )

    if config_path and contracts:
        click.echo(
            click.style(
                "Error: cannot use both --config and positional contracts", fg="red"
            )
        )
        sys.exit(1)
    if agent_id and not config_path:
        click.echo(click.style("Error: --agent requires --config", fg="red"))
        sys.exit(1)
    if not config_path and not contracts:
        click.echo("Usage: sponsio eval TRACE_PATH [CONTRACTS...] [--config FILE]")
        sys.exit(1)

    # Resolve contracts to a flat list of NL strings / structured entries
    contract_list: list = []
    if config_path:
        from sponsio.config import load_config

        cfg = load_config(config_path)
        if not agent_id:
            if len(cfg.agents) == 1:
                agent_id = next(iter(cfg.agents))
            else:
                click.echo(
                    click.style(
                        f"Error: multiple agents in config "
                        f"({list(cfg.agents.keys())}), use --agent",
                        fg="red",
                    )
                )
                sys.exit(1)
        for ce in cfg.agents[agent_id].contracts:
            for field_value in (ce.assumption, ce.guarantee):
                if field_value is None:
                    continue
                if isinstance(field_value, list):
                    contract_list.extend(field_value)
                else:
                    contract_list.append(field_value)
    else:
        contract_list = list(contracts)

    cases = discover_cases(trace_path)
    if not cases:
        click.echo(click.style(f"No trace files found at {trace_path}", fg="yellow"))
        sys.exit(0)

    report = run_eval(cases, contract_list)

    # Validate flag combinations BEFORE doing the eval render so a
    # typo doesn't cost the user a 30s replay.
    if (max_fpr_delta is not None or max_fnr_delta is not None) and not baseline_path:
        click.echo(
            click.style(
                "Error: --max-fpr-delta / --max-fnr-delta require --baseline",
                fg="red",
            )
        )
        sys.exit(2)

    diff = None
    if baseline_path:
        try:
            baseline_data = json.loads(baseline_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            click.echo(
                click.style(f"Error reading baseline {baseline_path}: {e}", fg="red")
            )
            sys.exit(2)
        diff = diff_reports(baseline_data, report)

    if as_json:
        # Preserve the long-standing flat shape (report fields at the
        # top) when there's no baseline. every existing script
        # depends on ``data["n_safe"]`` etc.  Only when a baseline
        # IS present do we add a sibling key for the diff, which
        # callers can look up only when they passed ``--baseline``.
        out = report.to_dict()
        if diff is not None:
            out["baseline_diff"] = diff.to_dict()
        click.echo(json.dumps(out, indent=2))
    else:
        click.echo(format_report(report))
        if diff is not None:
            click.echo(format_diff(diff))

    # Snapshot the report for the next PR's --baseline.  Done AFTER
    # the gate check so a regression-failing run doesn't auto-poison
    # main's baseline (gate failures should not silently rewrite the
    # standard you're being measured against).
    gate_failures: list[str] = []
    if diff is not None:
        gate_failures = diff.gate_violations(
            max_fpr_delta=max_fpr_delta,
            max_fnr_delta=max_fnr_delta,
        )
        if gate_failures:
            click.echo()
            for v in gate_failures:
                click.secho(f"  ✗ {v}", fg="red", bold=True)

    if write_baseline_path and not gate_failures:
        write_baseline_path.write_text(json.dumps(report.to_dict(), indent=2))
        click.secho(f"\n  ✓ baseline written to {write_baseline_path}", fg="green")
    elif write_baseline_path and gate_failures:
        click.secho(
            f"\n  · skipped writing {write_baseline_path} "
            "(gate failed. fix the regression first)",
            fg="yellow",
        )

    if gate_failures:
        sys.exit(1)


@cli.command()
@click.argument(
    "target",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=".",
    required=False,
)
@click.option(
    "--plan",
    "plan_spec",
    default=None,
    help=(
        "Print the would-run commands for these picks, don't run them.  "
        "Used by IDE-agent wizard prompts for the dry-run preview step."
    ),
)
@click.option(
    "--apply",
    "apply_spec",
    default=None,
    help=(
        "Run the commands for these picks non-interactively.  Picks "
        "format: ``framework=<name>;ides=<ide>:<level>,<ide>:<level>;"
        "mode=observe|enforce`` where ``<level>`` is one of ``none`` / "
        "``skill`` / ``full``.  Legacy ``hosts=`` / ``skills=`` form is "
        "still accepted (``hosts=X`` ↔ ``X:full``, ``skills=X`` ↔ "
        "``X:skill``)."
    ),
)
@click.option(
    "--no-demo",
    is_flag=True,
    help="Skip the post-install demo offer.",
)
def init(
    target: Path,
    plan_spec: str | None,
    apply_spec: str | None,
    no_demo: bool,
):
    """Interactive 4-axis onboarding wizard.

    Walks you through the four decisions that actually matter on
    first run:

    \b
      1. framework wrap        which agent framework to wrap (or "none")
      2. protect host agents   install hooks for claude-code / cursor / openclaw
      3. install Sponsio skill drop SKILL.md into IDEs not picked above
      4. mode                  observe (shadow, default) or enforce (block)

    Three modes:

    \b
      sponsio init                     # interactive TTY (humans)
      sponsio init --plan '<picks>'    # print commands, don't run
      sponsio init --apply '<picks>'   # run commands non-interactively

    Picks string format::

        framework=<name>;ides=<ide>:<level>,<ide>:<level>;mode=<observe|enforce>

    Where ``<level>`` is ``none`` / ``skill`` / ``full``.  Legacy
    ``hosts=<a>,<b>;skills=<a>,<b>`` form is still accepted.

    Examples:\n
        sponsio init\n
        sponsio init --plan 'framework=langgraph;ides=cursor:skill;mode=observe'\n
        sponsio init --apply 'framework=langgraph;ides=cursor:skill;mode=observe'
    """
    from sponsio.init_wizard import (
        apply_commands,
        detect_environment,
        offer_demo,
        parse_picks,
        plan_commands,
        run_interactive,
    )

    if plan_spec is not None and apply_spec is not None:
        raise click.UsageError("--plan and --apply are mutually exclusive")

    target_dir = target if target.is_dir() or not target.suffix else target.parent
    env = detect_environment(target_dir)
    # Detect a pre-existing ``@sponsio/sdk`` install (via ``npm
    # install`` OR ``npm link``) so we can skip the redundant
    # install step in plan.  Skipping is critical for ``npm link``
    # workflows. running ``npm install --save-dev`` against a
    # linked package overwrites the symlink with the published
    # release, silently undoing the user's local-source testing.
    # The legacy ``@sponsio/scan-ts`` package is now a deprecation
    # shim that re-exports ``@sponsio/sdk``'s CLI; counting it as
    # "installed" lets users on the old name finish ``sponsio
    # init`` without an extra install round-trip.
    # The package dir alone isn't enough: a half-broken install can
    # leave ``node_modules/@sponsio/sdk/`` populated but the bin
    # symlink ``node_modules/.bin/sponsio`` missing. which would
    # then make ``npx sponsio onboard`` fall through to the npm
    # public registry (404, since pip-side ``sponsio`` shadows the
    # name).  Require BOTH for the skip-install path.
    _scan_ts_pkg = (target_dir / "node_modules" / "@sponsio" / "sdk").exists() or (
        target_dir / "node_modules" / "@sponsio" / "scan-ts"
    ).exists()
    _scan_ts_bin = (target_dir / "node_modules" / ".bin" / "sponsio").exists()
    _scan_ts_installed = _scan_ts_pkg and _scan_ts_bin

    # ---- non-TTY paths: --plan / --apply ----
    if plan_spec is not None:
        picks = parse_picks(plan_spec)
        cmds = plan_commands(
            picks,
            ts_project=env.runtime == "ts",
            scan_ts_already_installed=_scan_ts_installed,
        )
        if not cmds:
            click.echo(
                "Nothing to do. picks select no framework wrap and no "
                "IDE protection.  Re-run with at least one ``framework=`` "
                "or ``ides=<ide>:full|skill``."
            )
            return
        for cmd in cmds:
            click.echo("would run: " + " ".join(cmd))
        return

    if apply_spec is not None:
        picks = parse_picks(apply_spec)
    else:
        picks = run_interactive(env)

    cmds = plan_commands(
        picks,
        ts_project=env.runtime == "ts",
        scan_ts_already_installed=_scan_ts_installed,
    )
    if not cmds:
        click.echo()
        click.secho(
            "Nothing to install. every axis was set to 'none'.  "
            "Re-run `sponsio init` and pick at least one framework "
            "wrap or IDE level (skill / full).",
            fg="yellow",
        )
        return

    # Dry-run preview before running, even on the interactive path.
    # gives the user a final chance to spot a wrong pick.  Indented
    # col-2 to match the wizard's body content margin (banner col-0,
    # everything below at col-2).
    click.echo()
    click.secho("  preview", bold=True, fg="cyan")
    for cmd in cmds:
        click.echo("    → " + " ".join(cmd))
    click.echo()

    # Skip the confirm gate when called via ``--apply``. the IDE
    # agent already showed me the dry-run preview before invoking,
    # and a second confirmation here would corrupt structured output.
    if apply_spec is None:
        from sponsio.init_wizard import _confirm as _wizard_confirm

        if not _wizard_confirm("Run these?", default=True):
            click.echo()
            click.secho("  ✘  No changes made.", fg="yellow")
            click.echo(
                "      Re-run `sponsio init` whenever you're ready, "
                "or pass\n      `sponsio init --plan '<picks>'` to "
                "preview the commands without prompts."
            )
            return

    rc = apply_commands(cmds)
    if rc != 0:
        sys.exit(rc)

    # Picks-aware "what now" block.  Each combination of axes leaves
    # the user in a different spot. IDE-only installs especially
    # were ending without any concrete next action.  Route through
    # the helper so each path gets a tailored handoff.
    from sponsio.init_wizard import print_next_steps as _print_next_steps

    _print_next_steps(picks, ts_project=env.runtime == "ts")

    if not no_demo:
        offer_demo()


# ---------------------------------------------------------------------------
# refresh. re-mine contracts from recent traces and merge into sponsio.yaml
# ---------------------------------------------------------------------------


@cli.command()
@click.argument(
    "path",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=".",
    required=False,
)
@click.option(
    "--llm",
    is_flag=True,
    help=(
        "Make a real LLM call to verify connectivity, latency, and "
        "credentials.  Opt-in because it costs a few tokens and ~1s; "
        "default ``doctor`` is fully offline."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help=(
        "Emit a structured JSON report instead of the human-readable "
        "table.  Schema is stable per `schema_version`.  Use for "
        "IDE integrations, CI gates, fleet dashboards, or piping into "
        "`jq` / wrapper scripts."
    ),
)
def doctor(path: Path, llm: bool, as_json: bool):
    """Diagnose your Sponsio install and project wiring.

    Runs a short battery of mostly-offline checks. Python version,
    sponsio import sanity, optional SDK availability, LLM credentials,
    ``sponsio.yaml`` validation, a project-level AST scan, and an
    end-to-end guard smoke-test. and prints a single report telling
    you exactly what to run next.

    Pass ``--llm`` to also make a real LLM round-trip (uses the
    provider/key from ``sponsio.yaml``'s ``extractor:`` section if
    present, env-var auto-detection otherwise).

    Exits non-zero if any check fails (warnings are advisory and don't
    change the exit code), so ``doctor`` is safe to wire into CI as a
    pre-flight sanity gate.

    Examples:\n
        sponsio doctor\n
        sponsio doctor src/\n
        sponsio doctor --llm\n
        sponsio doctor path/to/sponsio.yaml --llm
    """
    from sponsio.doctor import print_report, report_to_dict, run_doctor

    results, exit_code = run_doctor(path, with_llm=llm)
    if as_json:
        # Suppress the human-readable banner. JSON consumers want
        # exactly one parseable document on stdout, nothing else.
        click.echo(json.dumps(report_to_dict(results, exit_code), indent=2))
    else:
        print_report(results)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# bench
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# onboard
# ---------------------------------------------------------------------------


def _parse_existing_contracts(yaml_path: Path, agent_id: str) -> list[dict]:
    """Extract the on-disk yaml's contracts so ``--emit-context``
    consumers can dedupe their semantic-pass proposals.

    Pulls only the fields a deduper actually needs (pattern, args,
    source) and only from the named agent's block.  Conservative:
    on any parse error, returns an empty list. a malformed yaml
    will still surface elsewhere (doctor, validate), no need to
    block the diagnostic JSON over it.

    Each returned dict has the shape::

        {"pattern": "arg_blacklist",
         "args": ["delete_snapshot", "path", ["...", "..."]],
         "source": "scan" | "library:tier1.shell" | "agent-extracted" | ...}

    Pack-included rules (resolved via ``include:``) are NOT walked
    here. the host agent only needs to dedupe against rules
    actually written into THIS yaml (the inline ``contracts:``
    block).  Pack rules round-trip through ``include:`` and the
    template's "don't inline what the pack already covers" rule
    keeps them out of the agent's proposals.
    """
    try:
        import yaml as _yaml
    except ImportError:
        return []

    try:
        text = yaml_path.read_text(encoding="utf-8")
        data = _yaml.safe_load(text)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return []
    agent_block = agents.get(agent_id)
    if not isinstance(agent_block, dict):
        return []
    contracts = agent_block.get("contracts")
    if not isinstance(contracts, list):
        return []

    out: list[dict] = []
    for c in contracts:
        if not isinstance(c, dict):
            continue
        # Contracts can be written ``- G: {...}`` or ``- A: {...}, G: {...}``.
        # We pull from whichever has the pattern.
        g = _contract_guarantee(c)
        body = g if isinstance(g, dict) else c
        if not isinstance(body, dict):
            continue
        pattern = body.get("pattern")
        if not isinstance(pattern, str):
            continue
        out.append(
            {
                "pattern": pattern,
                "args": body.get("args") or [],
                "source": body.get("source") or c.get("source") or "",
            }
        )
    return out


@cli.command()
@click.argument(
    "target",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=".",
    required=False,
)
@click.option(
    "--agent",
    "agent_id",
    default="agent",
    show_default=True,
    help=(
        "Agent identifier stamped into sponsio.yaml.  Matches "
        "`sponsio scan`'s default so a later `scan --append` lands "
        "in the same agent block."
    ),
)
@click.option(
    "--mode",
    type=click.Choice(["observe", "enforce"]),
    default=None,
    help=(
        "Runtime mode written into sponsio.yaml.  Skip the flag to be "
        "prompted interactively (same Y/N question ``sponsio init`` "
        "and ``sponsio host install`` ask).  ``observe`` is the safe "
        "default. never blocks, logs every would-have-blocked decision "
        "to ~/.sponsio/sessions/<agent_id>/*.jsonl."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing sponsio.yaml without prompting.",
)
@click.option(
    "--no-probe-ollama",
    is_flag=True,
    help=(
        "Skip the localhost:11434 liveness probe.  Useful in CI or "
        "behind strict firewalls where the <500ms probe still times "
        "out slowly and you'd rather jump straight to the starter pack."
    ),
)
@click.option(
    "--no-doctor",
    is_flag=True,
    help=(
        "Skip the post-onboard `sponsio doctor` run.  By default we "
        "run the full offline check battery so users see whether the "
        "install is healthy before they switch to enforce mode."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the structured OnboardReport as JSON instead of text.",
)
@click.option(
    "--emit-context",
    "emit_context",
    is_flag=True,
    default=False,
    help=(
        "Skip the LLM step and instead emit the structured inputs "
        "(framework / tool inventory / auto-selected packs / existing "
        "yaml / discovered policy docs) as JSON to stdout. Used by the "
        "host agent driving the ``sponsio`` skill: pair with "
        "``sponsio prompt onboard`` and apply in the agent's own LLM "
        "context. no UnifiedExtractor call, no extra API key."
    ),
)
@click.option(
    "--push/--no-push",
    default=False,
    help=(
        "After writing sponsio.yaml, push it to the local dashboard at "
        "--push-url so it lands on the Scan page + Contract Library "
        "(default: off; on is one round-trip per run, silently skipped "
        "when the dashboard isn't up)."
    ),
)
@click.option(
    "--push-url",
    default="http://127.0.0.1:8000",
    help="Dashboard URL to push to (default: http://127.0.0.1:8000).",
)
@click.option(
    "--interactive/--no-interactive",
    "interactive",
    default=None,
    help=(
        "Prompt for framework / LLM provider / model up front and "
        "write `.sponsiorc` + `.env.example` next to sponsio.yaml. "
        "Default: auto. interactive when stdin is a TTY, "
        "non-interactive otherwise (CI, scripts, docker entrypoints, "
        "``--json``, ``--emit-context``).  Pass ``--no-interactive`` "
        "to force the silent path even from a terminal."
    ),
)
def onboard(
    target: Path,
    agent_id: str,
    mode: str | None,
    force: bool,
    no_probe_ollama: bool,
    no_doctor: bool,
    as_json: bool,
    emit_context: bool,
    push: bool,
    push_url: str,
    interactive: bool | None,
):
    """One-shot project wire-up. detect framework, write sponsio.yaml, print patch.

    Composes `init` + `scan` + `doctor` into a single command so
    first-time users don't have to learn three subcommands just to
    run the guard in observe mode.  Specifically:

    \b
      1. Detects the agent framework from imports + dependencies.
      2. Detects the best available LLM provider (env vars →
         OPENAI_BASE_URL → local Ollama → none).
      3. Writes sponsio.yaml in observe mode with an inferred contract
         set. LLM-inferred when a provider was found, or pure name-
         heuristic starter pack when it wasn't.
      4. Prints the framework-specific 2-line patch the user needs to
         apply to their agent entry point.

    Safe defaults throughout: mode=observe (never blocks on day 1),
    agent_id="agent" (matches `sponsio scan`), and --force off (the
    "I already have sponsio.yaml" case is louder than a silent overwrite).

    Examples:\n
        sponsio onboard\n
        sponsio onboard src/\n
        sponsio onboard . --agent customer_bot\n
        sponsio onboard --force --no-probe-ollama
    """
    from sponsio.onboard import OnboardReport, run_onboard
    from sponsio.runtime.spinner import Spinner

    # Branded header. same ``header_banner`` Rich primitive that
    # ``sponsio init`` / ``sponsio doctor`` / runtime print_banner /
    # explain renderers use, so onboarding's first line of output
    # looks like the rest of the CLI instead of a hand-glued
    # ``━`` string.  Skipped on the non-interactive structured-
    # output paths (--json, --emit-context) so consumers parsing
    # stdout don't have to sed past it.
    if not as_json and not emit_context and not os.environ.get("SPONSIO_INIT_DISPATCH"):
        # Standalone ``sponsio onboard`` prints a full banner so users
        # see the product wordmark when running it directly.  When the
        # ``sponsio init`` wizard dispatched us, the preview block
        # above already showed ``→ sponsio onboard . --mode ...`` so
        # any additional banner / divider here is redundant.  Skip
        # entirely and let the first stage section rule
        # (``Scanning your code ─────...``) carry the transition.
        from sponsio.render.components import header_banner as _header_banner
        from sponsio.runtime.terminal import (
            _make_stderr_console as _make_console,
        )

        _hdr_console = _make_console(None)
        _hdr_console.print()
        _hdr_console.print(_header_banner(tagline="onboard"))

    # One spinner per command. long-wait emits (``…``-suffixed) start
    # it, the next emit (or the final ``stop()`` after run_onboard)
    # cleans up.  Skipped silently when stderr isn't a TTY, so CI / pipe
    # / docker output stays line-oriented.
    _spinner = Spinner()

    def _progress(msg: str) -> None:
        # ``▸`` prefix = stage section header.  Render as a
        # :func:`section_rule` (label + ``─────...`` rule) so the
        # divider matches the runtime trace renderer + ``sponsio init``
        # axis headers.  Anything else is a per-step progress line.
        # dim cyan ``· `` bullet.  Emits ending with ``…`` are "this
        # will take a while" announcements; we hand them to the
        # spinner so the user sees motion during the wait.
        if as_json or emit_context:
            return
        # Always stop any running spinner first so the next line lands
        # cleanly (rather than on top of a stale frame).
        _spinner.stop()
        if msg.startswith("▸ "):
            from sponsio.render.components import (
                indent as _indent_for_progress,
                section_rule as _section_rule_for_progress,
            )
            from sponsio.runtime.terminal import (
                _make_stderr_console as _make_console_for_progress,
            )

            _progress_console = _make_console_for_progress(None)
            _progress_console.print()
            _progress_console.print(
                _indent_for_progress(_section_rule_for_progress(msg.removeprefix("▸ ")))
            )
            return
        # Bullets indent col-2 to match the section rule above them
        # and the trace + report renderers' body indentation.
        line = "  " + click.style("· ", fg="cyan", dim=True) + msg
        if msg.endswith("…"):
            _spinner.start(line)
        else:
            click.echo(line, err=True)

    # ---- agent-driven path: dump inputs, skip LLM step ------------------
    # ``--emit-context`` runs the deterministic stages (framework /
    # provider / AST tool inventory / pack selection) and stops short of
    # the LLM contract-mining inside CodeAnalyzer.generate_yaml.  The
    # host agent picks up where we leave off using ``sponsio prompt
    # onboard``.
    if emit_context:
        target_path = Path(target)
        if target_path.suffix in {".yaml", ".yml"}:
            root = target_path.parent or Path(".")
            existing_yaml_path = target_path
        else:
            root = target_path
            existing_yaml_path = target_path / "sponsio.yaml"

        from sponsio.discovery.extractors.code_analysis import CodeAnalyzer
        from sponsio.onboard import detect_framework, select_packs

        # AST-only. explicit ``use_llm=False`` so this path never
        # reads any provider env var.
        analyzer = CodeAnalyzer(use_llm=False)
        tool_inventory = analyzer.get_tool_inventory([str(root)]) or []
        # Run framework detection AFTER tool inventory, prioritizing
        # the files the extractor already pinned as agent code. fixes
        # the monorepo case where 200+ pad files at the root could
        # exhaust the framework scan cap before the agent file (in a
        # deep subdir) was reached, leaving framework="none" even
        # though tool_inventory had found ``@tool`` functions there.
        prioritize_files: list[Path] = []
        for t in tool_inventory:
            fp = t.get("filepath") if isinstance(t, dict) else None
            if not fp:
                continue
            p = Path(fp)
            if not p.is_absolute():
                p = root / p
            if p.is_file():
                prioritize_files.append(p)
        framework = detect_framework(root, prioritize_files=prioritize_files)
        pack_selection = select_packs(framework.framework, tool_inventory)

        existing_yaml_text = ""
        if existing_yaml_path.exists():
            try:
                existing_yaml_text = existing_yaml_path.read_text(encoding="utf-8")
            except OSError:
                pass

        # Surface common policy docs the agent should weight in the
        # extraction.  Conservative search. root-level only, by
        # convention. to avoid pulling in unrelated repo prose.  Dedup
        # by inode so case-insensitive filesystems (macOS HFS+) don't
        # report ``security.md`` and ``SECURITY.md`` twice.
        policy_docs = []
        seen_inodes: set[tuple[int, int]] = set()
        for candidate in ("security.md", "SECURITY.md", "policy.md", "POLICY.md"):
            p = root / candidate
            if not p.is_file():
                continue
            try:
                stat = p.stat()
                key = (stat.st_dev, stat.st_ino)
                if key in seen_inodes:
                    continue
                seen_inodes.add(key)
                policy_docs.append(
                    {
                        "path": str(p.relative_to(root)),
                        "content": p.read_text(encoding="utf-8"),
                    }
                )
            except OSError:
                pass

        # Pull the framework-specific wrap snippet (the 2-3 line patch
        # the user pastes into their agent entry file).  The skill's
        # W1 step 5 references this field; emitting it here lets the
        # agent surface the wiring instructions in the same turn it
        # writes the YAML.
        wrap_snippet_text = ""
        try:
            from sponsio.onboard import _wrap_snippet  # type: ignore[attr-defined]

            wrap_snippet_text = _wrap_snippet(framework.framework, agent_id) or ""
        except Exception:  # pragma: no cover. best-effort
            pass

        # Locate likely agent entry files so the IDE agent doesn't have
        # to re-discover them. Conservative regex grep over root-level
        # .py files, ranked by signal density.
        entry_file_candidates: list[dict] = []
        try:
            framework_signals: dict[str, list[re.Pattern]] = {
                "langchain": [
                    re.compile(r"from\s+langchain"),
                    re.compile(r"create_react_agent\s*\("),
                ],
                "langgraph": [
                    re.compile(r"from\s+langgraph"),
                    re.compile(r"StateGraph\s*\("),
                    re.compile(r"create_react_agent\s*\("),
                ],
                "crewai": [re.compile(r"from\s+crewai"), re.compile(r"\bAgent\s*\(")],
                "autogen": [
                    re.compile(r"from\s+autogen"),
                    re.compile(r"AssistantAgent\s*\("),
                ],
                "openai_agents": [
                    re.compile(r"from\s+agents"),
                    re.compile(r"\bAgent\s*\("),
                ],
                "openai": [re.compile(r"from\s+openai"), re.compile(r"OpenAI\s*\(")],
                "anthropic": [
                    re.compile(r"from\s+anthropic"),
                    re.compile(r"Anthropic\s*\("),
                    re.compile(r"messages\.create\s*\("),
                ],
                "claude_agent_sdk": [re.compile(r"from\s+claude_agent_sdk")],
                "google_adk": [re.compile(r"from\s+google\.adk")],
            }
            sigs = framework_signals.get(framework.framework, [])
            if sigs:
                from glob import glob as _glob

                py_files = sorted(
                    set(
                        _glob(str(root / "*.py"))
                        + _glob(str(root / "**/*.py"), recursive=True)
                    )
                )
                py_files = [
                    f
                    for f in py_files
                    if "/.venv/" not in f
                    and "/__pycache__/" not in f
                    and "/site-packages/" not in f
                ]
                scored = []
                for f in py_files[:200]:  # cap to avoid scanning large monorepos
                    try:
                        text = Path(f).read_text(encoding="utf-8")
                    except OSError:
                        continue
                    matches = [s.pattern for s in sigs if s.search(text)]
                    if matches:
                        scored.append(
                            {
                                "path": str(Path(f).relative_to(root)),
                                "reason": "matches: " + ", ".join(matches),
                            }
                        )
                scored.sort(key=lambda x: -len(x["reason"]))
                entry_file_candidates = scored[:5]
        except Exception:  # pragma: no cover. best-effort
            entry_file_candidates = []

        # Parse the on-disk sponsio.yaml's contracts (if any) so the
        # host agent driving this can dedupe its semantic-pass
        # proposals without having to re-grep YAML.  Conservative
        # parse. failures degrade to an empty list rather than
        # blocking the emit (a malformed yaml is worth surfacing,
        # but not at the cost of also blocking the rest of the
        # diagnostic JSON).
        pre_existing_contracts: list[dict] = []
        if existing_yaml_path.exists():
            pre_existing_contracts = _parse_existing_contracts(
                existing_yaml_path, agent_id
            )

        # Health flag. the host agent uses this as the single
        # gate for "should I keep going or stop and ask?".
        # Reflects three orthogonal failure modes the previous
        # case-A/B/C check in the wizard prompt was hand-rolling.
        if framework.framework != "none" and tool_inventory:
            health = "ok"
            health_detail = "framework + tools detected"
        elif tool_inventory:
            # Rare after the prioritize-files fix, but still possible
            # for unusual import shapes (star imports, dynamic
            # `__import__`, etc.). surface explicitly so the agent
            # asks the user to pick from axis 1 manually.
            health = "tools_only"
            health_detail = (
                "tool_inventory found tools but no framework import "
                "matched any known signature. pick framework manually"
            )
        elif framework.framework != "none":
            health = "tools_only"
            health_detail = (
                f"framework {framework.framework!r} detected but "
                "tool_inventory is empty. agent likely uses external "
                "SDK tools (MCP, prebuilt LangChain tools, OpenAI "
                "JSON schemas); grep the repo for tool registration"
            )
        else:
            health = "empty"
            health_detail = (
                "no framework, no tools. wrong scan path "
                "(monorepo + agent in subdir), or this is a bare "
                "function-calling loop, or the project is TS and "
                "you ran the Python probe"
            )

        click.echo(
            json.dumps(
                {
                    "health": health,
                    "health_detail": health_detail,
                    "framework": {
                        "name": framework.framework,
                        "evidence": framework.evidence,
                    },
                    "agent_id": agent_id,
                    "tool_inventory": tool_inventory,
                    "auto_selected_packs": list(pack_selection.packs),
                    "needs_workspace": pack_selection.needs_workspace,
                    "existing_yaml": existing_yaml_text,
                    "pre_existing_contracts": pre_existing_contracts,
                    "policy_docs": policy_docs,
                    "wrap_snippet": wrap_snippet_text,
                    "entry_file_candidates": entry_file_candidates,
                    "out_path": str(existing_yaml_path),
                    "next_steps_hint": (
                        "Run ``sponsio prompt onboard`` to get the prompt "
                        "template, apply it to this JSON in your own LLM "
                        "context, then write the resulting YAML to "
                        f"{existing_yaml_path} via Edit/Write, and patch "
                        "the agent entry file (see entry_file_candidates) "
                        "with the wrap_snippet."
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    # ---- interactive setup (prompts + dotfile writes) ------------------
    # Decide whether to run prompts.  --json and --emit-context force
    # non-interactive (prompts would corrupt the structured output).
    # Otherwise an explicit --interactive / --no-interactive flag wins;
    # without one, follow the TTY: real shell → prompts, CI / pipe /
    # docker entrypoint → silent.
    from sponsio.onboard import _wrap_snippet  # type: ignore[attr-defined]
    from sponsio.onboard import detect_framework as _detect_fw_for_prompts
    from sponsio.onboard import detect_provider as _detect_prov_for_prompts
    from sponsio.onboard_setup import (
        SetupAnswers,
        maybe_no_api_key_warning,
        run_setup_prompts,
        stdin_is_tty,
        write_sponsiorc,
    )
    from sponsio.sponsiorc import load_sponsiorc

    if as_json or emit_context:
        is_interactive = False
    elif interactive is not None:
        is_interactive = interactive
    else:
        is_interactive = stdin_is_tty()

    # Resolve runtime mode through the same shared helper that
    # ``sponsio host install`` uses, so all install paths ask the
    # observe-vs-enforce question the same way. ``--mode`` skips the
    # prompt; ``--json`` / ``--emit-context`` / ``--no-interactive``
    # also skip it (structured-output paths must not pollute stdout
    # with a click prompt). Fallback when no signal: ``observe``.
    mode_was_explicit = mode is not None
    mode = _resolve_runtime_mode(mode, allow_prompt=is_interactive)

    target_dir = target if target.is_dir() else target.parent

    # Resolve where sponsio.yaml will live so we can detect a "second
    # run" case below without duplicating run_onboard's path logic.
    if target.suffix in {".yaml", ".yml"}:
        out_path_check = target
    else:
        out_path_check = target_dir / "sponsio.yaml"
    yaml_already_exists = out_path_check.exists() and not force

    # Second-run UX: if the user already ran onboard here (.sponsiorc is
    # present), skip the prompts and reuse the saved choices.  Re-asking
    # every time was annoying and the user explicitly flagged it.
    rc = load_sponsiorc(target_dir) if target_dir.exists() else None
    rc_in_target = (
        rc is not None
        and rc.found
        and rc.source_path is not None
        and rc.source_path.parent.resolve() == target_dir.resolve()
    )

    if rc_in_target:
        # Reuse the rcfile values verbatim. that's the whole point of
        # the dotfile.  Prompts only fire when there's nothing to reuse.
        # We still run framework detection so the wrap snippet on the
        # yaml-preserve path reflects current code (not a stale
        # rcfile).  Detection beating rcfile here is intentional: the
        # only way ``framework`` ends up wrong in an rcfile is when an
        # older detection couldn't recognise the user's code; if today's
        # detector finds something concrete, that's the better answer.
        pre_fw = _detect_fw_for_prompts(target_dir) if target_dir.exists() else None
        detected_fw = (
            pre_fw.framework if pre_fw and pre_fw.framework != "none" else None
        )
        answers = SetupAnswers(
            framework=detected_fw or rc.framework or "none",
            provider=rc.extractor_provider or "none",
            model=rc.extractor_model or "",
            api_key_env=rc.extractor_api_key_env or "",
        )
        pre_prov = None
    else:
        # Pre-detect framework + provider so the prompts have sensible
        # defaults.  Cheap (no LLM); run even in non-interactive mode
        # so the rcfile we write below reflects what onboard actually
        # used.
        pre_fw = _detect_fw_for_prompts(target_dir) if target_dir.exists() else None
        pre_prov = _detect_prov_for_prompts(probe_ollama=not no_probe_ollama)
        answers = run_setup_prompts(
            detected_framework=pre_fw.framework if pre_fw else "none",
            detected_provider=pre_prov.provider,
            detected_model=pre_prov.model or "",
            detected_api_key_env=pre_prov.env_var or "",
            interactive=is_interactive,
        )

    # Second-run UX: existing sponsio.yaml + no --force → preserve it.
    # We still refresh the dotfiles + reprint the wrap snippet so the
    # command stays useful (re-running onboard to remind yourself how
    # to wire it up shouldn't error).  --force keeps the regenerate
    # path for users who actually want a fresh yaml.
    report: OnboardReport | None = None
    if yaml_already_exists:
        if not as_json and not emit_context:
            click.echo()
            click.secho(f"✓ {out_path_check}", fg="green")
            click.echo("  preserved (re-run with --force to regenerate)")
    else:
        try:
            report = run_onboard(
                target,
                agent_id=agent_id,
                mode=mode,
                force=force,
                probe_ollama=not no_probe_ollama,
                run_doctor=not no_doctor,
                progress=_progress,
            )
        except FileExistsError as e:
            _spinner.stop()
            click.echo(click.style("Error: ", fg="red") + str(e), err=True)
            sys.exit(1)
        finally:
            # Belt + braces: if the last emit was a ``…`` line (rare.
            # run_onboard normally pairs each "Running …" with a "done"
            # emit), make sure we don't leave the spinner thread spinning
            # forever and the cursor stuck on a stale frame.
            _spinner.stop()

    # Write the rcfile (idempotent, plain write_text).  Skipped when
    # target was a single file rather than a directory. the rcfile
    # location is ambiguous in that case.  We deliberately do NOT
    # write a ``.env.example`` here: sponsio reads ``os.environ``
    # directly (no python-dotenv in the runtime), so a ``.env``-based
    # recipe would silently fail.  Users keep secrets in their shell
    # rc / direnv / system keychain. the rcfile records only the
    # variable name (``api_key_env``), not the value.
    sponsiorc_path: Path | None = None
    if target_dir.exists() and target_dir.is_dir():
        sponsiorc_path = write_sponsiorc(answers, target_dir)

    if as_json:
        payload = (
            report.to_dict()
            if report is not None
            else {
                "out_path": str(out_path_check),
                "preserved": True,
            }
        )
        payload["setup"] = {
            "interactive": is_interactive,
            "framework": answers.framework,
            "provider": answers.provider,
            "model": answers.model,
            "api_key_env": answers.api_key_env,
            "api_key_set_in_env": answers.api_key_set_in_env,
            "sponsiorc_path": str(sponsiorc_path) if sponsiorc_path else None,
        }
        click.echo(json.dumps(payload, indent=2))
        return

    # Human-readable summary.  Kept compact so the wrap snippet is the
    # last thing the user sees. it's what they need to act on.  When
    # report is None we're on the second-run preserve path; the "✓
    # sponsio.yaml preserved" line was already printed above.
    if report is not None:
        click.echo()
        click.secho(f"  ✓ {report.out_path}", fg="green")
        click.echo(f"      tools:      {report.tools_count}")
        click.echo(f"      contracts:  {report.contracts_count}")
        click.echo(f"      mode:       {report.mode}")
        click.echo(f"      framework:  {report.framework.framework}")
        click.echo(f"      provider:   {report.provider.provider}")
        if report.starter_pack_used:
            click.secho(
                "      · starter-pack applied (no-LLM safety net)",
                fg="yellow",
                dim=True,
            )

    # Dotfiles written alongside sponsio.yaml.  Surface the paths so
    # the user knows which file holds their tool config (vs. the
    # contract library) and where to drop their actual API key.
    if sponsiorc_path is not None:
        click.echo()
        click.secho(f"  ✓ {sponsiorc_path}", fg="green")
        click.echo(
            "      framework + LLM config. edit this file to change "
            "framework / model / api_key_env"
        )
        # Best-effort .gitignore hint: only fire when sponsiorc is in
        # a git repo AND `.sponsiorc` isn't already covered by the
        # existing rules.  Avoids nagging users who already gitignore'd
        # it (or who deliberately track it for team-wide config).
        try:
            rc_dir = sponsiorc_path.parent
            git_root = rc_dir
            for _ in range(8):  # walk up to 8 levels. plenty for a repo
                if (git_root / ".git").exists():
                    break
                if git_root.parent == git_root:
                    git_root = None  # type: ignore[assignment]
                    break
                git_root = git_root.parent
            else:
                git_root = None
            if git_root is not None:
                gitignore = git_root / ".gitignore"
                already_ignored = False
                if gitignore.is_file():
                    ignore_text = gitignore.read_text(encoding="utf-8")
                    for line in ignore_text.splitlines():
                        s = line.strip()
                        if s and not s.startswith("#"):
                            if s in {".sponsiorc", "**/.sponsiorc", "*.sponsiorc"}:
                                already_ignored = True
                                break
                if not already_ignored:
                    click.secho(
                        "  tip: add `.sponsiorc` to .gitignore "
                        "(holds local model / api_key_env hints)",
                        fg="cyan",
                        dim=True,
                    )
        except OSError:
            pass
    # No-key warning. fires when the user picked a provider that
    # needs a key but the env var isn't actually set, or when
    # provider==none (so onboard fell back to the name-heuristic
    # starter pack instead of LLM-inferred contracts).
    no_key_msg = maybe_no_api_key_warning(answers)
    if no_key_msg is not None:
        click.echo()
        for ln in no_key_msg.splitlines():
            click.secho("  " + ln, fg="yellow")

    if report is not None and report.doctor_results is not None:
        total = len(report.doctor_results)
        fails = sum(1 for r in report.doctor_results if r.status == "fail")
        warns = sum(1 for r in report.doctor_results if r.status == "warn")
        if fails == 0 and warns == 0:
            click.secho(f"  ✓ doctor:   {total}/{total} checks passed", fg="green")
        else:
            click.echo(
                f"  doctor:     {total - fails - warns}/{total} ok"
                + (click.style(f", {warns} warn", fg="yellow") if warns else "")
                + (click.style(f", {fails} fail", fg="red") if fails else "")
            )
            for r in report.doctor_results:
                if r.status in {"fail", "warn"}:
                    color = "red" if r.status == "fail" else "yellow"
                    click.echo(
                        f"    {click.style(r.icon, fg=color)} {r.name}: {r.detail}"
                    )

    if report is not None:
        for w in report.warnings:
            click.echo()
            click.echo(click.style("  warn: ", fg="yellow") + w)

    # Print the framework-specific patch snippet.  Auto-applying it
    # to the user's agent file used to live behind ``--apply`` but
    # was removed (only langgraph / langchain were supported, and a
    # coding agent / manual paste does the same job for any
    # framework with fewer surprises).  On the second-run preserve
    # path the framework comes from the rcfile-derived answers (the
    # user's saved choice, not a fresh detection).
    snippet = (
        report.wrap_snippet
        if report is not None
        else _wrap_snippet(answers.framework or "none", agent_id)
    )
    click.echo()
    click.secho("Add this to your agent entry point:", bold=True)
    click.echo()
    for ln in snippet.splitlines():
        click.echo(f"  {click.style(ln, fg='cyan')}")

    # Surface the contract file the user should now review. ``onboard``
    # wrote LLM-inferred (or starter-pack) contracts based on detected
    # tools. they're a sane first cut, not a finished policy. Pointing
    # the user at the path with a clear "review before flipping to
    # enforce" callout turns "did onboard actually do what I wanted?"
    # into one ``cat`` command.
    review_path = (
        report.out_path
        if report is not None
        else (out_path_check if yaml_already_exists else None)
    )
    if review_path is not None and not as_json and not emit_context:
        click.echo()
        click.secho("Review the generated contracts:", bold=True)
        click.echo(f"  {click.style(str(review_path), fg='green')}")
        click.secho(
            "  (open it, sanity-check each rule, then re-run with `--mode enforce`",
            dim=True,
        )
        click.secho("   when you're ready to switch from observe to active)", dim=True)

    # --push: surface the generated yaml in the local dashboard (one
    # command == everything the dashboard needs). Silently skipped if
    # the dashboard isn't running, so a CI invocation without `serve`
    # up doesn't fail.
    if push and report is not None:
        try:
            yaml_content = report.out_path.read_text()
        except Exception as e:
            click.echo(
                click.style("\n  push skipped: ", fg="yellow")
                + f"could not read {report.out_path} ({e})"
            )
        else:
            click.echo()
            _push_scan_to_dashboard(
                yaml_content=yaml_content,
                filename=report.out_path.name,
                dashboard_url=push_url,
                source_paths=[str(target)],
            )

    # Optional immediate flip-to-enforce prompt.  Onboard always
    # writes ``mode: observe`` by default. that's the safe path for
    # teams who want a soak period.  But some users (CI hardening
    # workflows, demo recordings, "I already ran the agent and know
    # the contracts are right") want enforce on day 1.  Asking here
    # turns "remember to sed the yaml later" into one keystroke.
    #
    # Skipped when:
    #   - non-interactive (no TTY / --no-interactive / --json /
    #     --emit-context. prompts would corrupt structured output)
    #   - the user already chose ``--mode enforce`` (no point asking
    #     a question they answered on the command line)
    #   - run_onboard didn't actually produce a report (early-exit
    #     paths above)
    if (
        report is not None
        and is_interactive
        and not as_json
        and not emit_context
        and not mode_was_explicit  # honor caller's `--mode` choice
        and not os.environ.get("SPONSIO_INIT_DISPATCH")
        and report.mode == "observe"
    ):
        click.echo()
        flip = click.confirm(
            click.style(
                "Mode is `observe` (shadow). Flip to `enforce` now?",
                bold=True,
            ),
            default=False,
            show_default=True,
        )
        if flip:
            try:
                yaml_text = report.out_path.read_text(encoding="utf-8")
                # Defer to the shared helper so both the interactive
                # onboard flow and the explicit ``sponsio mode`` CLI
                # agree on which mode-line to patch (runtime preferred,
                # defaults fallback, append-observe-only as last resort).
                new_yaml, action = _patch_mode_in_yaml(yaml_text, "enforce")
                if action == "unchanged":
                    click.secho(
                        f"  ✓ {report.out_path} is already `mode: enforce`",
                        fg="green",
                    )
                elif action == "missing":
                    # ``onboard`` always writes a defaults.mode line so
                    # this branch is defensive; surface it as a
                    # yellow warning rather than overwriting.
                    click.secho(
                        f"  ✗ no `mode:` line in {report.out_path}, leaving "
                        f"as observe. Add a `runtime: mode: enforce` block "
                        f"by hand to flip.",
                        fg="yellow",
                    )
                else:
                    report.out_path.write_text(new_yaml, encoding="utf-8")
                    suffix = (
                        " (appended runtime: block)"
                        if action == "appended"
                        else f" ({action}.mode)"
                    )
                    click.secho(
                        f"  ✓ flipped {report.out_path} → mode: enforce{suffix}",
                        fg="green",
                    )
            except OSError as e:
                click.secho(f"  ✗ could not rewrite {report.out_path}: {e}", fg="red")

    click.echo()
    click.echo("Next:")
    click.echo("  sponsio report --since 24h            # what would have been blocked")
    click.echo(
        "  sponsio mode enforce                  # one-shot flip when you're ready"
    )
    click.echo()


# ---------------------------------------------------------------------------
# `sponsio plugin ...`. host-plugin runtime adapter
# ---------------------------------------------------------------------------
# `sponsio prompt <flow>`. workflow prompts for host-agent driving
# ---------------------------------------------------------------------------
#
# Counterpart to ``sponsio plugin prompt <host>``: prints the agent-facing
# extraction prompt for a top-level workflow (``onboard`` / ``scan``).
# The setup skill at ``sponsio/skills/sponsio/SKILL.md`` calls this so the
# host agent (Claude Code, Cursor, Codex) can apply the prompt in its own
# LLM context against the JSON emitted by ``sponsio onboard --emit-context``
# or ``sponsio scan --emit-context``.


def _patch_mode_in_yaml(text: str, target_mode: str) -> tuple[str, str]:
    r"""Set ``mode:`` to ``target_mode`` in a ``sponsio.yaml`` text body.

    The Python loader reads ``runtime.mode`` AND ``defaults.mode``;
    the TS loader reads only ``runtime.mode``. To stay correct under
    both, this prefers the ``runtime.mode`` line, then falls back to
    ``defaults.mode``, then appends a fresh ``runtime:`` block when
    neither exists.

    A naive ``re.subn(r"^\s*mode:")`` is wrong: it patches whichever
    ``mode:`` line happens to come first in the file. If a yaml has
    both ``runtime.mode`` and ``defaults.mode`` and ``defaults`` is
    listed first, the runtime line silently stays stale and the TS
    loader keeps reading the old value. Walking parent keys avoids
    that whole class of bug.

    Safety policy on appending: when neither block exists, this helper
    will append a fresh ``runtime:`` block ONLY for ``target_mode ==
    "observe"`` (the safe default). For ``target_mode == "enforce"``
    against a malformed / missing-mode yaml, it returns ``"missing"``
    instead of writing, so callers can refuse to silently flip a yaml
    they cannot verify into the blocking posture. This matches the
    OWASP-style principle of explicit-opt-in for enforcement, and
    preserves CI scripts that relied on the old exit-1 behaviour for
    malformed configs.

    Returns:
        ``(new_text, action)`` where ``action`` is one of
        ``"runtime"`` / ``"defaults"`` / ``"appended"`` / ``"unchanged"``
        / ``"missing"``. ``"unchanged"`` means the file already had
        the desired value. ``"missing"`` means no mode line exists and
        target is ``enforce`` so the helper refused to append.
    """
    lines = text.splitlines(keepends=True)
    current_parent: str | None = None
    runtime_idx = -1
    defaults_idx = -1
    mode_line_re = re.compile(r"^(\s+)mode:\s*(observe|enforce)(\s*(?:#.*)?)$")

    for i, raw in enumerate(lines):
        line = raw.rstrip("\n").rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            # Top-level key like ``runtime:`` / ``defaults:`` / ``agents:``.
            # Track only the most recent top-level key; only valid yaml
            # mappings reach here so ``key:`` parsing is safe.
            if ":" in stripped:
                current_parent = stripped.split(":", 1)[0].strip()
            continue
        if mode_line_re.match(line):
            if current_parent == "runtime" and runtime_idx < 0:
                runtime_idx = i
            elif current_parent == "defaults" and defaults_idx < 0:
                defaults_idx = i

    target_idx = runtime_idx if runtime_idx >= 0 else defaults_idx
    if target_idx >= 0:
        action = "runtime" if target_idx == runtime_idx else "defaults"
        raw = lines[target_idx]
        new_raw = re.sub(
            r"^(\s+mode:\s*)(observe|enforce)(\s*(?:#.*)?)$",
            lambda m: f"{m.group(1)}{target_mode}{m.group(3)}",
            raw.rstrip("\n"),
        )
        # Preserve original line-ending.
        ending = raw[len(raw.rstrip("\n")) :]
        lines[target_idx] = new_raw + ending
        new_text = "".join(lines)
        if new_text == text:
            return text, "unchanged"
        return new_text, action

    # Neither block had a mode line. Append only for the safe default
    # (observe). Refuse to materialise an enforce block out of thin
    # air: a missing mode line is suspicious, and silently flipping
    # such a yaml into the blocking posture would mask CI / config
    # errors that the operator should fix by hand.
    if target_mode != "observe":
        return text, "missing"
    # Using ``runtime:`` (not ``defaults:``) so the TS loader picks it
    # up too. Trailing newline normalised so the appended block doesn't
    # glue to the previous line.
    suffix = "" if text.endswith("\n") or text == "" else "\n"
    appended = f"{suffix}\nruntime:\n  mode: {target_mode}\n"
    return text + appended, "appended"


@cli.command(name="mode")
@click.argument(
    "target_mode",
    metavar="MODE",
    type=click.Choice(["observe", "enforce"]),
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("sponsio.yaml"),
    show_default=True,
    help="Path to the sponsio.yaml whose mode should be flipped.",
)
def cmd_mode(target_mode: str, config_path: Path):
    """Flip a sponsio.yaml between `observe` and `enforce` in one shot.

    The expected workflow is:

    \b
        sponsio onboard .            # writes sponsio.yaml in observe
        # ...soak in observe for a day or two, watch `sponsio report`...
        sponsio mode enforce         # one-line flip when you're ready

    Prefers to update the ``runtime.mode:`` line (which both the Python
    and TS loaders read), falling back to ``defaults.mode:`` (Python
    only) or inserting a fresh ``runtime:`` block when neither exists.
    Comments and surrounding lines survive untouched.
    """
    text = config_path.read_text(encoding="utf-8")
    new_text, action = _patch_mode_in_yaml(text, target_mode)
    if action == "unchanged":
        click.echo(
            click.style(
                f"✓ {config_path} is already `mode: {target_mode}` (no change)",
                fg="green",
                dim=True,
            )
        )
        return
    if action == "missing":
        # No mode line in the yaml AND target is enforce. Refuse to
        # create one out of thin air. Tell the operator how to fix it
        # safely (run observe first, edit yaml, or re-run onboard).
        click.echo(
            click.style(
                f"✗ no `mode:` line found in {config_path} and refusing to "
                f"append a fresh ``runtime: mode: enforce`` block. Run "
                f"`sponsio mode observe` first to materialise the block, "
                f"then `sponsio mode enforce` to flip it. Or edit the yaml "
                f"by hand, or re-run `sponsio onboard --force`.",
                fg="red",
            ),
            err=True,
        )
        raise SystemExit(1)
    config_path.write_text(new_text, encoding="utf-8")
    if action == "appended":
        click.echo(
            click.style("✓ ", fg="green")
            + f"{config_path} → mode: {target_mode} (appended runtime: block)"
        )
    else:
        click.echo(
            click.style("✓ ", fg="green")
            + f"{config_path} → {action}.mode: {target_mode}"
        )


@cli.command(name="prompt")
@click.argument(
    "flow",
    type=click.Choice(["onboard", "scan"]),
)
def cmd_prompt(flow: str):
    """Print the agent-facing prompt template for a sponsio workflow.

    Used by the ``sponsio`` skill (``W1``. initial setup, ``W2``.
    audit & refine) to drive the host agent through contract authoring
    without burning a separate LLM API call.

    Pair with the corresponding ``--emit-*`` flag:

    \b
        sponsio onboard . --emit-context     # structured input for prompt
        sponsio prompt onboard               # the prompt itself

    \b
        sponsio scan src/ --emit-context
        sponsio prompt scan

    The agent reads both, applies the prompt to the JSON in its own
    context, and writes the result via Edit/Write.  No
    ``UnifiedExtractor`` / API key needed for this path.
    """
    from importlib.resources import files

    pkg = files("sponsio.prompts")
    click.echo(pkg.joinpath(f"{flow}.md").read_text(encoding="utf-8"))


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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    cli()


if __name__ == "__main__":
    main()
