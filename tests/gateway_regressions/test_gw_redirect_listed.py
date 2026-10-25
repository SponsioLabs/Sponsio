"""tools/list and tools/call disagree for a tool behind a stopping
verdict other than ``blocked``: ``allowed_tools`` (via
``BaseGuard.filter_tools``) keeps a tool whose probe is ``redirected``,
while the call path (``evaluate``) denies every ``stop_original``
verdict. The gateway lists a tool that it will refuse on every call."""

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
      - desc: "send goes to check instead"
        G:
          pattern: redirect_to_safe
          args: [mcp__pay__send, mcp__pay__check]
"""


@pytest.fixture
def svc():
    d = Path(tempfile.mkdtemp(prefix="sp-rv3-", dir="/tmp"))
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
    yield s
    s.close_gateway()
    shutil.rmtree(d, ignore_errors=True)


def test_a_listed_tool_is_one_the_call_path_admits(svc):
    gw = svc.gateway
    listed = [t["name"] for t in gw.list_tools("s1")["tools"]]
    out = gw.call_tool("s1", "pay__send", {})
    refused = bool(out["result"].get("isError"))
    # Either the tool is not offered, or a call to it is admitted.
    assert not ("pay__send" in listed and refused), (listed, out["result"])
