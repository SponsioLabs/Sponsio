"""When the daemon cannot send a tools/call reply (it exceeds the frame
cap), it closes the connection; the shim takes that as "no daemon",
"restarts" it and sends the same tools/call again, so the upstream tool
runs twice for one call from the host."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.protocol import MAX_FRAME_BYTES
from sponsio.daemon.server import DaemonServer
from sponsio.guard_stdin import GuardPolicy
from sponsio.shim import mcp_shim_path

FAKE = Path(__file__).parent / "fake_upstream.py"


@pytest.fixture
def daemon():
    d = Path(tempfile.mkdtemp(prefix="sp-gw-", dir="/tmp"))
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
                        "env": {
                            "CALL_LOG": str(log),
                            "BIG_BYTES": str(MAX_FRAME_BYTES + 1024),
                        },
                        "timeout_s": 30,
                    }
                }
            }
        )
    )
    svc = GuardService(
        root=root, state_dir=state, policy=GuardPolicy(mode="enforce", on_error="deny")
    )
    server = DaemonServer(d / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        yield d, server.socket_path, log
    finally:
        server.stop(join_timeout_s=0.5)
        svc.close_gateway()
        shutil.rmtree(d, ignore_errors=True)


def test_one_host_call_runs_the_upstream_tool_once(daemon):
    d, sock, log = daemon
    # `--sponsio /usr/bin/true`: the shim's "start a daemon" is a no-op
    # spawn, as it is when a daemon is already running on the socket.
    proc = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            str(mcp_shim_path()),
            "--socket",
            str(sock),
            "--timeout",
            "60",
            "--sponsio",
            "/usr/bin/true",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(d)},
    )
    try:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "pay__big", "arguments": {}},
        }
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        reply = json.loads(proc.stdout.readline())
        assert reply["id"] == 1
        # The daemon says the result was too large; the host sees an error.
        assert reply["result"]["isError"] is True
        assert "larger than" in reply["result"]["content"][0]["text"]
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    calls = [json.loads(x)["name"] for x in log.read_text().splitlines()]
    assert calls == ["big"]


def test_a_reply_lost_after_sending_is_not_sent_again(tmp_path):
    """A daemon that takes the call and drops the connection (it crashed,
    say): the shim reports it and does not resend."""
    import socket
    import threading

    from sponsio.shim import sponsio_mcp

    sock_path = Path(tempfile.mkdtemp(prefix="sp-gw-", dir="/tmp")) / "s"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(8)
    seen: list[bytes] = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                seen.append(conn.recv(65536))  # take the request, answer nothing

    threading.Thread(target=serve, daemon=True).start()
    opts, timeout = sponsio_mcp.parse_args(
        ["--socket", str(sock_path), "--sponsio", "/usr/bin/true", "--timeout", "10"]
    )
    out = []

    class _Out:
        def write(self, s):
            out.append(s)

        def flush(self):
            pass

    bridge = sponsio_mcp.Bridge(opts, timeout, _Out())
    try:
        reply = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "pay__send", "arguments": {}},
            }
        )
    finally:
        srv.close()
        shutil.rmtree(sock_path.parent, ignore_errors=True)
    assert reply["result"]["isError"] is True
    assert "not sent again" in reply["result"]["content"][0]["text"]
    assert len(seen) == 1
