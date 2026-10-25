"""A call the upstream refused at the protocol level (JSON-RPC error:
the tool did not run) still counts in the session's trace, so a
``must_precede`` whose prerequisite never ran is satisfied."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from sponsio.daemon.guard_service import GuardService
from sponsio.guard_stdin import GuardPolicy

FAKE = Path(__file__).parent / "fake_upstream.py"

PAY = """
version: "1"
agents:
  pay:
    contracts:
      - desc: "check before send"
        G:
          pattern: must_precede
          args: [mcp__pay__check, mcp__pay__send]
"""


@pytest.fixture
def svc():
    d = Path(tempfile.mkdtemp(prefix="sp-rv1-", dir="/tmp"))
    root = d / "plugins"
    (root / "pay").mkdir(parents=True)
    (root / "pay" / "sponsio.yaml").write_text(PAY)
    state = d / "state"
    state.mkdir()
    log = d / "calls.jsonl"
    (state / "gateway.yaml").write_text(
        json.dumps(
            {
                "servers": {
                    "pay": {
                        "command": [sys.executable, str(FAKE)],
                        "env": {"CALL_LOG": str(log)},
                        "timeout_s": 10,
                    }
                }
            }
        )
    )
    s = GuardService(
        root=root, state_dir=state, policy=GuardPolicy(mode="enforce", on_error="deny")
    )
    yield s, log
    s.close_gateway()
    shutil.rmtree(d, ignore_errors=True)


def _calls(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [json.loads(x)["name"] for x in log.read_text().splitlines()]


def test_a_prerequisite_the_upstream_refused_does_not_unlock_the_next_tool(svc):
    s, log = svc
    gw = s.gateway
    failed = gw.call_tool("s1", "pay__check", {"reject": True})
    assert failed["result"]["isError"] is True
    assert _calls(log) == []  # `check` never ran upstream
    # `send` must still be behind `check`.
    assert "pay__send" not in [t["name"] for t in gw.list_tools("s1")["tools"]]
    out = gw.call_tool("s1", "pay__send", {})
    assert out["result"].get("isError") is True
    assert _calls(log) == []
