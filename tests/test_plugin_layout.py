"""Static tests for the ``plugins/sponsio-claude-code/`` plugin layout.

The plugin is loaded by Claude Code via ``--plugin-dir``; the
manifest, hooks, skills, and libraries all have to obey schemas
Claude Code enforces at load time. We catch regressions here so a
typo in ``plugin.json`` doesn't surface only when an end user
runs the plugin and gets a silent ``plugin failed to load``.

Schemas covered:

* ``.claude-plugin/plugin.json`` — required ``name``; optional
  ``description``, ``version``, ``author``.
* ``hooks/hooks.json`` — top-level ``hooks`` mapping with at least
  one event matcher.
* ``skills/<name>/SKILL.md`` — YAML frontmatter with ``name`` and
  ``description``; non-empty body.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "sponsio-claude-code"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_manifest_exists():
    assert (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").exists()


def test_manifest_required_fields():
    data = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert data.get("name") == "sponsio-claude-code"
    # description shows in the plugin manager — required to be non-empty.
    assert data.get("description")


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def test_hooks_pretooluse_calls_sponsio():
    """Every PreToolUse handler must shell out to ``sponsio plugin guard``.

    A regression here means tool calls aren't getting evaluated at
    all — silent failure. The check is intentionally narrow: any
    other command would skip Sponsio entirely.
    """
    data = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    pretooluse = data["hooks"]["PreToolUse"]
    assert pretooluse, "no PreToolUse matchers defined"
    for matcher in pretooluse:
        for h in matcher["hooks"]:
            assert h["type"] == "command", h
            assert "sponsio plugin guard" in h["command"], h["command"]
            assert "--stdin" in h["command"], h["command"]


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


SKILLS_DIR = PLUGIN_ROOT / "skills"


def _skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())


def test_setup_skill_shipped():
    """The unified setup skill is the entry point for first-time UX.

    Previously split across ``setup`` and ``scan``; merged into
    ``setup`` because they share Mode A context (host-level
    ``~/.sponsio/plugins/`` configuration) and the user's session
    typically touches both halves. The split caused trigger ambiguity
    and forced cross-references between two near-identical skill
    bodies.
    """
    names = {p.name for p in _skill_dirs()}
    assert "setup" in names

    # Merged-in coverage check: the setup description must mention
    # both the install (bundled-starter) and scan (unbundled-plugin)
    # halves so Claude Code triggers it for either entry phrase.
    setup_md = (SKILLS_DIR / "setup" / "SKILL.md").read_text()
    assert "scan" in setup_md.lower()
    assert "install" in setup_md.lower()


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_skill_md_has_valid_frontmatter(skill_dir: Path):
    """Every skill must have YAML frontmatter with name + description.

    Claude Code uses ``description`` to decide when to model-invoke
    the skill, so a missing or stub description means the agent
    won't pick it up automatically — silent UX regression.
    """
    md = skill_dir / "SKILL.md"
    assert md.exists(), f"missing SKILL.md under {skill_dir}"
    text = md.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, f"{md} missing frontmatter delimiters"
    fm = m.group(1)

    name_match = re.search(r"^name:\s*(\S+)", fm, re.MULTILINE)
    assert name_match, f"{md} frontmatter missing `name:`"
    assert name_match.group(1) == skill_dir.name, (
        f"{md} name={name_match.group(1)!r} does not match dir name={skill_dir.name!r}"
    )

    # description: may be a single-line scalar, or a multi-line
    # block — the latter would start with ``description:`` and the
    # text continuing to end-of-frontmatter. Either way, must be
    # non-empty after the colon.
    desc_match = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
    assert desc_match, f"{md} frontmatter missing `description:`"
    assert len(desc_match.group(1)) >= 30, (
        f"{md} description is too short (<30 chars). Claude Code "
        f"uses description for model-invocation triggers — needs "
        f"keywords."
    )


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_skill_body_is_substantial(skill_dir: Path):
    """A SKILL.md with no body is a stub — likely committed empty."""
    text = (skill_dir / "SKILL.md").read_text()
    body = re.split(r"^---\n.*?\n---\n", text, maxsplit=1, flags=re.DOTALL)[-1]
    assert len(body.strip()) > 200, (
        f"{skill_dir.name}/SKILL.md body looks empty or stub-like"
    )


# ---------------------------------------------------------------------------
# Library mirror — confirm structural symmetry, not byte equality
# (byte equality is in test_plugin_install.py for each named starter).
# ---------------------------------------------------------------------------


def test_each_library_dir_has_sponsio_yaml():
    """Every directory under ``libraries/`` must contain ``sponsio.yaml``."""
    libs = PLUGIN_ROOT / "libraries"
    for child in libs.iterdir():
        if child.is_dir():
            assert (child / "sponsio.yaml").exists(), f"{child} missing sponsio.yaml"
