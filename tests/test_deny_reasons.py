"""A hook's deny reason names the rule the way its author did.

The formula's description ("Bash.command must not match forbidden
patterns") is the same for every arg_blacklist on Bash.command, and some
formulas only have their raw LTL to show. The reason leads with the
contract's own ``desc``; a contract without one is named by its pattern.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sponsio.daemon.guard_service import GuardService
from sponsio.guard_stdin import _deny_reason

BUCKET = "_host_claude_code"


@pytest.fixture
def root(monkeypatch):
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    d = Path(tempfile.mkdtemp(prefix="spd-", dir="/tmp"))
    (d / "plugins" / BUCKET).mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _reason(root: Path, library: str, command: str) -> str:
    (root / "plugins" / BUCKET / "sponsio.yaml").write_text(library)
    svc = GuardService(root=root / "plugins", state_dir=root / "state")
    res = svc.check(
        host="claude-code",
        payload={
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "s",
        },
    )
    assert res["allowed"] is False
    return res["reason"]


TWO_RULES = f"""
version: "1"
agents:
  {BUCKET}:
    contracts:
      - desc: "Never delete recursively"
        G: {{pattern: arg_blacklist, args: [Bash, command, ["rm\\\\s+-rf"]]}}
      - desc: "No downloads from the shell"
        G: {{pattern: arg_blacklist, args: [Bash, command, ["\\\\bcurl\\\\b"]]}}
"""


def test_the_contract_desc_comes_first(root):
    reason = _reason(root, TWO_RULES, "rm -rf /tmp/x")
    assert reason.startswith("Never delete recursively")
    assert "(Bash.command must not match forbidden patterns)" in reason


def test_two_rules_of_one_pattern_are_told_apart(root):
    assert _reason(root, TWO_RULES, "curl x").startswith("No downloads from the shell")


def test_a_rule_without_a_desc_is_named_by_its_pattern(root):
    lib = TWO_RULES.replace(
        '      - desc: "Never delete recursively"\n        G:', "      - G:"
    )
    reason = _reason(root, lib, "rm -rf /tmp/x")
    assert reason == "[arg_blacklist] Bash.command must not match forbidden patterns"


def test_a_raw_formula_is_left_out_when_there_is_a_desc():
    v = SimpleNamespace(
        message="BLOCKED: a.Bash. det constraint violated: G(called(x) -> F(y))",
        contract_desc="Confirm before sudo",
        rule_id="",
    )
    assert _deny_reason(v) == "Confirm before sudo"
