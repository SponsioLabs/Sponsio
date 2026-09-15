"""The shell capability bundle's ``rm`` rules cover long-form flags.

Regression tests for an external audit finding (Trenyx, 2026-09): the
"Ban recursive deletes of sensitive roots" rule carried four regexes
that all required a single ``-[rRf]+`` cluster, so ``rm --recursive
--force /`` passed. ``arg_blacklist`` is a raw regex with no argv
parsing, so every accepted spelling has to be in the pattern itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PACK = (
    Path(__file__).resolve().parents[1]
    / "sponsio"
    / "contracts"
    / "capability"
    / "shell.yaml"
)


def _patterns(desc_fragment: str) -> list[str]:
    doc = yaml.safe_load(PACK.read_text())
    rule = next(
        r for r in doc["agents"]["*"]["contracts"] if desc_fragment in r["desc"]
    )
    return rule["G"]["args"][2]


def _hits(command: str, patterns: list[str]) -> bool:
    return any(re.search(p, command) for p in patterns)


# ---------------------------------------------------------------------------
# § 1 "Ban recursive deletes of sensitive roots"
# ---------------------------------------------------------------------------

ROOTS = _patterns("recursive deletes of sensitive roots")

BANNED = [
    # the spellings the rule always caught
    "rm -rf /",
    "rm -fr /",
    "rm -Rf /",
    "rm -rf ~",
    "rm -rf $HOME",
    "rm -rf ${HOME}",
    "rm -rf /*",
    "rm -rf /home/me",
    "rm -rf /root",
    "rm -rf /Users/me",
    "sudo rm -rf /",
    "ls; rm -rf /",
    # the audit finding: long-form and split flags
    "rm --recursive --force /",
    "rm --force --recursive /",
    "rm --recursive /",
    "rm -r --force /",
    "rm --recursive $HOME",
    "rm --recursive --force /*",
    "rm --recursive /Users/me",
    "rm -r -f /",
    "rm -f -r /",
    "rm -rfv /",
    "rm -rf -- /",
    "rm -rf --no-preserve-root /",
    "rm --no-preserve-root -rf /",
    "/bin/rm -rf /",
    "rm -rf ~/",
]

ALLOWED = [
    "rm -rf ./build",
    "rm -rf /tmp/scratch",
    "rm --recursive ./node_modules",
    "rm file.txt",
    "rm -i /",
    "rm -v /",
    "rm -rf ~/.cache/pip",
    "perform -rf /",
    "echo rm -rf /x",
]


def test_root_patterns_compile():
    for p in ROOTS:
        re.compile(p)


@pytest.mark.parametrize("command", BANNED)
def test_recursive_delete_of_root_is_matched(command):
    assert _hits(command, ROOTS), command


@pytest.mark.parametrize("command", ALLOWED)
def test_ordinary_rm_is_not_matched(command):
    assert not _hits(command, ROOTS), command


# ---------------------------------------------------------------------------
# The other two rm rules had the same short-flag-only gap
# ---------------------------------------------------------------------------


def test_line_continuation_rule_sees_long_flags():
    pats = _patterns("line-continuation")
    assert _hits("ls \\\n rm -rf /", pats)
    assert _hits("ls \\\n rm --recursive /", pats)
    assert not _hits("ls \\\n rm -v scratch.txt", pats)


def test_undefined_variable_rule_sees_long_flags():
    pats = _patterns("undefined-variable")
    assert _hits("rm -rf {foo}/{bar}", pats)
    assert _hits("rm --recursive --force {foo}/{bar}", pats)
    assert _hits("rm -rf ${foo}/", pats)


# ---------------------------------------------------------------------------
# End to end: the shipped pack, through a guard, blocks the long form
# ---------------------------------------------------------------------------


def test_pack_blocks_long_form_through_guard(tmp_path):
    from sponsio.integrations.base import BaseGuard

    cfg = tmp_path / "sponsio.yaml"
    cfg.write_text("agents:\n  bot:\n    include: [sponsio:capability/shell]\n")
    guard = BaseGuard(agent_id="bot", config=str(cfg), mode="enforce")

    assert guard.guard_before("exec", {"command": "rm --recursive --force /"}).blocked
    assert guard.guard_before("exec", {"command": "rm -r --force ~"}).blocked
    assert not guard.guard_before("exec", {"command": "rm -rf ./build"}).blocked


def test_host_library_blocks_long_form(tmp_path, monkeypatch):
    """Same path as ``test_plugin_init.test_default_library_blocks_rm_rf``
    but with the spelling the audit found."""
    repo_root = Path(__file__).resolve().parents[1]
    pkg = repo_root / "sponsio" / "plugin" / "defaults" / "_host.yaml"
    target = tmp_path / "_host" / "sponsio.yaml"
    target.parent.mkdir(parents=True)
    target.write_bytes(pkg.read_bytes())
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))

    from sponsio.guard_stdin import evaluate_event

    def run(command: str):
        return evaluate_event(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        )

    assert run("rm --recursive --force /").allowed is False
    assert run("rm -rf /").allowed is False
    assert run("rm -rf ./build").allowed is True
