"""Regression tests that pin ``SKILL.md`` to the actual CLI surface.

The bundled Agent Skill (``sponsio/skills/sponsio/SKILL.md``) makes
concrete promises about CLI subcommands and their flags — that's what
the agent dispatches on at runtime.  If the CLI drifts and the skill
doesn't, Cursor / Claude Code will paste syntax that doesn't exist.

These tests enforce two invariants:

A. **Public API surface contract** — the commands + flags the skill
   declares in its "Public API surface" section must actually exist
   in ``sponsio --help``.  Written as an explicit ``SURFACE`` table
   (not parsed from markdown) so the test is self-documenting and
   readable when it fails.

B. **No orphaned examples** — every ``sponsio <subcommand>`` that
   appears inside a code block in SKILL.md must map to a registered
   click subcommand.  Cheap way to catch typos like "sponsio repot"
   or mentions of commands that were removed.

Update these tests when you intentionally change the skill's
CLI contract; CI fails here precisely because that change matters.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

import pytest
from click.testing import CliRunner

from sponsio.cli import cli


# ---------------------------------------------------------------------------
# SKILL.md path — shared across tests
# ---------------------------------------------------------------------------


def _skill_md_path() -> Path:
    return Path(str(files("sponsio") / "skills" / "sponsio" / "SKILL.md"))


# ---------------------------------------------------------------------------
# A. Public API surface contract
# ---------------------------------------------------------------------------


# The shape is: (subcommand_chain, [flags that MUST appear in --help]).
# "Must appear" uses substring match so we're tolerant of click's
# reformatting (line wraps, indent changes) but strict about the flag
# existing at all.
#
# Keep this in sync with the "Public API surface" section of
# ``sponsio/skills/sponsio/SKILL.md``.  When you intentionally change
# the skill contract, update both places — CI will tell you which one
# drifted.
SURFACE: list[tuple[tuple[str, ...], list[str]]] = [
    # `onboard` no longer has any required surface flags — `--apply`
    # was removed (the printed snippet is self-explanatory enough that
    # auto-patching the agent file added more risk than value).  Empty
    # list means "command must exist, no flag invariants".
    (("onboard",), []),
    (
        ("scan",),
        ["--agent", "--llm", "--policy", "-t", "-o", "--append"],
    ),
    # ``refresh`` was moved to Sponsio Cloud — cross-trace pattern
    # mining is the cloud-side feature ``sponsio refresh`` backs. The
    # SKILL.md still describes the workflow narratively (W3b) so
    # contract authors know it exists, but the OSS CLI no longer
    # exposes the subcommand.
    (("validate",), ["--config", "--json"]),
    (("check",), ["--trace", "--config", "--agent"]),
    (("report",), ["--agent", "--since"]),
    (("doctor",), []),
    (("patterns",), []),
    (("packs",), []),
    (("skill", "install"), ["--tool", "--link", "--copy", "--dest"]),
    (
        ("plugin", "append"),
        ["--from", "--target", "--root", "--dry-run"],
    ),
]


@pytest.mark.parametrize("chain,required_flags", SURFACE, ids=lambda v: str(v))
def test_skill_surface_subcommand_exists_and_has_required_flags(
    chain: tuple[str, ...], required_flags: list[str]
):
    """For each (subcommand, flags) declared in ``SURFACE``, run
    ``sponsio <chain> --help`` and assert the flags are present.

    Failure here means SKILL.md is lying about the CLI: either the
    command or the flag vanished / renamed.  Fix SKILL.md and
    ``SURFACE`` in the same PR as the CLI change.
    """
    runner = CliRunner()
    argv = list(chain) + ["--help"]
    result = runner.invoke(cli, argv)

    assert result.exit_code == 0, (
        f"`sponsio {' '.join(chain)} --help` failed with "
        f"exit_code={result.exit_code}.  Output:\n{result.output}"
    )

    missing = [f for f in required_flags if f not in result.output]
    assert not missing, (
        f"`sponsio {' '.join(chain)} --help` is missing flag(s) "
        f"{missing!r} that SKILL.md promises.  Either restore the "
        f"flag(s) in sponsio/cli.py or update SKILL.md + this "
        f"test's SURFACE table.  Full --help output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# B. No orphaned examples — every "sponsio <subcommand>" referenced in
#    a SKILL.md code block must be a registered click command.
# ---------------------------------------------------------------------------


# Words that can follow ``sponsio`` and are NOT click subcommands.
# We filter these out so the orphan check has zero false positives.
_NOT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        # Global flags / pseudo-commands.
        "--version",
        "--help",
        "-h",
        # Words the skill uses narratively (not invocations).
        "CLI",
        "Desktop",
        "ships",
        "evaluates",
        "will",
        "can",
        "into",
        "YAML",
        "runtime",
        "auto",
        "skill",  # bare "sponsio skill" — the group, not a subcommand invocation
        # ``refresh`` and ``bench`` were moved out of OSS — refresh →
        # Sponsio Cloud (cross-trace pattern mining), bench deleted.
        # SKILL.md still mentions them in narrative context to explain
        # the cloud surface to users authoring contracts.
        "refresh",
        "bench",
    }
)


def _registered_subcommands() -> set[str]:
    """All top-level click command names under the ``sponsio`` group,
    plus ``skill install`` (two-word subcommand chain).

    Returning two-word chains in dot form ("skill.install") would make
    the orphan check more precise, but SKILL.md's examples are rarely
    that ambiguous — a plain set of leaf names is enough."""
    names: set[str] = set()
    for name, cmd in cli.commands.items():
        names.add(name)
        # Walk one level deep for click groups like ``skill``.
        sub = getattr(cmd, "commands", None)
        if sub:
            for sub_name in sub:
                names.add(sub_name)
    return names


# Match fenced code blocks; capture the language tag (if any) and the
# body separately so we can skip non-shell languages.  ``sponsio`` is
# a Python module *and* a CLI tool, so a python block like
# ``from sponsio import Sponsio`` would otherwise be a false positive.
_CODE_BLOCK_RE = re.compile(
    r"```([a-zA-Z0-9_-]*)\n(.*?)```",
    re.DOTALL,
)

# Languages whose content should be scanned for CLI invocations.  An
# empty lang tag is shell-by-convention in SKILL.md.
_SHELL_LANGS: frozenset[str] = frozenset({"", "bash", "sh", "shell", "console"})

# Extract the word that immediately follows ``sponsio ``.  Anchored at
# word boundaries so we don't pick up things like ``sponsiox``.
_SPONSIO_INVOCATION_RE = re.compile(r"\bsponsio\s+([A-Za-z][A-Za-z0-9_-]*)\b")


def test_skill_md_examples_reference_only_real_subcommands():
    """Scan SKILL.md for every ``sponsio <word>`` inside a fenced code
    block; assert each ``<word>`` is either a registered click
    subcommand or an explicitly allowlisted non-command token.

    This catches typos in SKILL.md (e.g. ``sponsio repot``) and stale
    references to commands that have been renamed or removed."""

    skill_md = _skill_md_path().read_text()
    known = _registered_subcommands()
    allowlist = _NOT_SUBCOMMANDS

    orphaned: set[str] = set()
    for block_match in _CODE_BLOCK_RE.finditer(skill_md):
        lang = block_match.group(1).lower()
        if lang not in _SHELL_LANGS:
            # ``python`` / ``yaml`` / ``ts`` blocks: sponsio appears as
            # a module import or a YAML key there, not as a CLI
            # invocation.  Skipping them avoids false positives.
            continue
        block = block_match.group(2)
        for m in _SPONSIO_INVOCATION_RE.finditer(block):
            word = m.group(1)
            if word in known or word in allowlist:
                continue
            orphaned.add(word)

    assert not orphaned, (
        f"SKILL.md references these ``sponsio <WORD>`` tokens in code "
        f"blocks that are NOT registered subcommands: {sorted(orphaned)}. "
        f"Known subcommands: {sorted(known)}.  Either fix the typo in "
        f"SKILL.md, register the command in sponsio/cli.py, or add the "
        f"token to _NOT_SUBCOMMANDS with a comment explaining why."
    )


def test_skill_md_mentions_every_promised_subcommand():
    """Flip-side check: every subcommand listed in ``SURFACE`` must
    actually be mentioned somewhere in SKILL.md.  If we added an
    entry to SURFACE but forgot to document it, fail loudly."""

    skill_md = _skill_md_path().read_text()
    missing: list[str] = []
    for chain, _flags in SURFACE:
        phrase = "sponsio " + " ".join(chain)
        if phrase not in skill_md:
            missing.append(phrase)

    assert not missing, (
        f"SURFACE declares these subcommands but SKILL.md never "
        f"mentions them: {missing}.  Either add coverage to SKILL.md "
        f"or remove them from SURFACE here."
    )
