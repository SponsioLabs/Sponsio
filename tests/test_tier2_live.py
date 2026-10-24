"""Tier 2 on a real system install, from the agent's side (P1.8).

Runs only with ``SPONSIO_TEST_TIER=2``, as an unprivileged user, on a
machine where ``sudo sponsio install --system`` has run: the CI job
``tier2`` does exactly that. Each test is something the agent's OS user
could try against Sponsio, and each must fail with a permission error,
while the agent socket keeps deciding calls.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SPONSIO_TEST_TIER") != "2",
    reason="needs a system install; runs with SPONSIO_TEST_TIER=2",
)


@pytest.fixture(scope="module")
def layout():
    from sponsio.system_install import installed_layout

    lay = installed_layout()
    assert lay is not None, "no system install on this machine"
    assert os.geteuid() != 0, "run as the agent's (unprivileged) user"
    return lay


def _write_fails(path) -> None:
    with pytest.raises(PermissionError):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("x")


def test_the_agent_socket_decides_calls(layout):
    from sponsio.daemon.client import DaemonClient

    client = DaemonClient(socket_path=layout.socket, timeout_s=10)
    assert client.call("ping", {})["pong"] is True
    out = client.call(
        "guard.check",
        {
            "host": "claude-code",
            "payload": {
                "hook_event_name": "PreToolUse",
                "session_id": "tier2-live",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
            },
        },
    )
    assert out["allowed"] is False and out["exit_code"] == 2


def test_the_admin_socket_is_closed(layout):
    with pytest.raises(PermissionError):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(layout.admin_socket))


def test_rules_history_and_log_are_out_of_reach(layout):
    with pytest.raises(PermissionError):
        os.listdir(layout.state)
    _write_fails(layout.plugins / "_host_claude_code" / "sponsio.yaml")
    _write_fails(layout.state / "daemon.yaml")
    _write_fails(layout.state / "audit.jsonl")


def test_engine_shims_and_wiring_are_root_owned(layout):
    for path in (
        layout.shim,
        layout.mcp_shim,
        layout.python,
        layout.service_file,
        layout.claude_settings,
    ):
        _write_fails(path)
    with pytest.raises(PermissionError):
        (layout.libexec / "planted.py").write_text("")


def test_the_daemon_cannot_be_signalled(layout):
    out = subprocess.run(
        ["pgrep", "-u", "_sponsio", "-f", "sponsio.cli daemon run"],
        capture_output=True,
        text=True,
    )
    pids = [int(p) for p in out.stdout.split()]
    assert pids, "no daemon running as _sponsio"
    for pid in pids:
        with pytest.raises(PermissionError):
            os.kill(pid, signal.SIGTERM)


def test_a_mode_change_from_the_agent_side_is_refused(layout):
    from sponsio.daemon.client import DaemonClient, DaemonError

    agent = DaemonClient(socket_path=layout.socket, timeout_s=10)
    with pytest.raises(DaemonError) as e:
        agent.call("mode.set", {"host": "claude-code", "mode": "observe"})
    assert e.value.code == "not_found"


def test_doctor_reports_tier_2(layout):
    out = subprocess.run(
        [sys.executable, "-m", "sponsio.cli", "doctor", "--tier", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    report = json.loads(out.stdout)
    failing = [
        g["name"] for g in report["guarantees"] if g["tier"] <= 2 and not g["ok"]
    ]
    assert report["tier"] >= 2, failing
