"""``sponsio skill`` — install/manage the bundled Agent Skill.

Also owns the shared skill-install verification helpers
(:func:`_packaged_skill_source`, :func:`_verify_skill_install_target`,
``_SKILL_TOOL_DIRS``), which ``sponsio doctor`` and ``sponsio host
install`` import.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import click

from sponsio.cli.app import cli


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
