"""``evaluate(library=...)`` applies ``gateway_inner_name`` even when the
caller named the library (the SDK's ``backend="daemon"``), while
``allowed_tools(library=...)`` probes the name as given. For an SDK whose
tools are named ``mcp__sponsio__<server>__<tool>`` (any client that
mounts the gateway shim under the name ``sponsio`` and uses Claude
Code's MCP naming), a contract in the SDK's library on that name hides
the tool in ``sdk.filter_tools`` but no longer matches in ``sdk.check``:
the call is admitted. The same library in-process blocks it."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from sponsio.daemon.guard_service import GuardService
from sponsio.guard_stdin import GuardPolicy

BOT = """
version: "1"
agents:
  bot:
    contracts:
      - desc: "check before send"
        G:
          pattern: must_precede
          args: [mcp__sponsio__acme__check, mcp__sponsio__acme__send]
"""

SEND = "mcp__sponsio__acme__send"


@pytest.fixture
def svc():
    d = Path(tempfile.mkdtemp(prefix="sp-rv7-", dir="/tmp"))
    root = d / "plugins"
    (root / "bot").mkdir(parents=True)
    (root / "bot" / "sponsio.yaml").write_text(BOT)
    s = GuardService(
        root=root,
        state_dir=d / "state",
        policy=GuardPolicy(mode="enforce", on_error="deny"),
    )
    yield s
    shutil.rmtree(d, ignore_errors=True)


def test_filter_and_check_agree_for_a_named_library(svc):
    offered = svc.allowed_tools("sdk", "sdk:s1", [SEND], "bot")
    assert offered == []  # filter_tools hides `send` until `check`
    outcome = svc.decide(
        {
            "host": "sdk",
            "session_id": "s1",
            "hook_event_name": "PreToolUse",
            "tool_name": SEND,
            "tool_input": {},
        },
        library="bot",
    )
    assert outcome.allowed is False, outcome
