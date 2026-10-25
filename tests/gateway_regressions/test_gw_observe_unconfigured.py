"""Observe mode: ``allowed_tools`` returns every tool before looking at
the library ("observe blocks nothing"), but ``evaluate`` still refuses a
tool with no library when unconfigured tools are denied. The gateway
lists the tool, then refuses every call to it, contrary to "in observe
mode every tool is listed and every call runs"."""

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


@pytest.fixture
def svc():
    d = Path(tempfile.mkdtemp(prefix="sp-rv4-", dir="/tmp"))
    root = d / "plugins"
    root.mkdir()
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
        root=root,
        state_dir=state,
        policy=GuardPolicy(mode="observe", on_error="deny", unconfigured="deny"),
    )
    yield s
    s.close_gateway()
    shutil.rmtree(d, ignore_errors=True)


def test_observe_mode_list_and_call_agree(svc):
    gw = svc.gateway
    listed = [t["name"] for t in gw.list_tools("s1")["tools"]]
    out = gw.call_tool("s1", "pay__check", {})
    refused = bool(out["result"].get("isError"))
    assert not ("pay__check" in listed and refused), (listed, out["result"])
