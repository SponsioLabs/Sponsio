"""The MCP gateway (P3.1): the daemon runs the upstream servers with
their credentials, lists only the tools the session's state allows, and
refuses any other call before it reaches the upstream.

The upstream is ``tests/fixtures/fake_mcp_server.py``; the end-to-end
tests run the real gateway shim the way a host would, as a subprocess
speaking MCP over stdio.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from sponsio.daemon.client import DaemonClient
from sponsio.daemon.gateway import (
    GatewayError,
    Upstream,
    parse_servers,
)
from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer
from sponsio.guard_stdin import GuardPolicy, gateway_inner_name
from sponsio.shim import mcp_shim_path

FAKE = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"

ACME = """
version: "1"
agents:
  acme:
    contracts:
      - desc: "check before send"
        G:
          pattern: must_precede
          args: [mcp__acme__check, mcp__acme__send]
      - desc: "never send to evil"
        G:
          pattern: arg_blacklist
          args: [mcp__acme__send, to, ["evil"]]
"""


@pytest.fixture
def short_tmp() -> Path:
    """Short-path tempdir for AF_UNIX binding (~104 char cap on macOS)."""
    d = Path(tempfile.mkdtemp(prefix="sp-gw-", dir="/tmp"))
    yield d
    for sub in sorted(d.rglob("*"), reverse=True):
        try:
            sub.rmdir() if sub.is_dir() else sub.unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass


def _write_gateway(state: Path, call_log: Path, **extra: object) -> None:
    state.mkdir(parents=True, exist_ok=True)
    servers = {
        "acme": {
            "command": [sys.executable, str(FAKE)],
            "env": {"ACME_TOKEN": "s3cret", "CALL_LOG": str(call_log)},
            "timeout_s": 10,
            **extra,
        }
    }
    (state / "gateway.yaml").write_text(json.dumps({"servers": servers}))


@pytest.fixture
def env(short_tmp: Path, monkeypatch):
    monkeypatch.delenv("ACME_TOKEN", raising=False)
    root = short_tmp / "plugins"
    (root / "acme").mkdir(parents=True)
    (root / "acme" / "sponsio.yaml").write_text(ACME)
    state = short_tmp / "state"
    call_log = short_tmp / "calls.jsonl"
    _write_gateway(state, call_log)
    svc = GuardService(
        root=root,
        state_dir=state,
        policy=GuardPolicy(mode="enforce", on_error="deny"),
    )
    yield svc, call_log
    svc.close_gateway()


def _calls(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [json.loads(line)["name"] for line in log.read_text().splitlines()]


def _names(reply: dict) -> list[str]:
    return [t["name"] for t in reply["tools"]]


def _text(result: dict) -> str:
    return " ".join(c.get("text", "") for c in result.get("content", []))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_server_entries_are_validated_one_by_one():
    servers = parse_servers(
        {
            "servers": {
                "good": {"command": "node server.js --flag", "env": {"K": 1}},
                "a__b": {"command": ["x"]},
                "_lead": {"command": ["x"]},
                "sponsio": {"command": ["x"]},
                "nocmd": {"env": {}},
                "badenv": {"command": ["x"], "env": ["K=v"]},
            }
        }
    )
    assert list(servers) == ["good"]
    assert servers["good"].command == ("node", "server.js", "--flag")
    assert servers["good"].env == (("K", "1"),)


def test_gateway_tools_seen_by_a_host_hook_are_decided_under_the_upstream_name():
    assert gateway_inner_name("mcp__sponsio__acme__send") == "mcp__acme__send"
    assert gateway_inner_name("MCP__Sponsio__acme__send") == "mcp__acme__send"
    assert gateway_inner_name("mcp__sponsio__lonely") == "mcp__sponsio__lonely"
    assert gateway_inner_name("mcp__acme__send") == "mcp__acme__send"


def test_a_hook_call_to_a_gateway_tool_meets_the_upstream_library(env):
    svc, _log = env
    out = svc.check(
        host="claude-code",
        payload={
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": "mcp__sponsio__acme__send",
            "tool_input": {"to": "bob"},
        },
    )
    assert out["allowed"] is False and "check before send" in out["reason"]


# ---------------------------------------------------------------------------
# The gateway in the daemon
# ---------------------------------------------------------------------------


def test_the_list_follows_the_sessions_state(env):
    svc, _log = env
    gw = svc.gateway
    first = gw.list_tools("s1")
    # Both pages are listed; `send` waits for `check`.
    assert _names(first) == ["acme__check", "acme__wipe"]
    out = gw.call_tool("s1", "acme__check", {})
    assert not out["result"].get("isError")
    assert out["digest"] != first["digest"]
    assert _names(gw.list_tools("s1")) == ["acme__check", "acme__send", "acme__wipe"]
    # Another session has its own state.
    assert _names(gw.list_tools("s2")) == ["acme__check", "acme__wipe"]


def test_a_tool_not_listed_cannot_be_called_by_name(env):
    svc, log = env
    out = svc.gateway.call_tool("s1", "acme__send", {"to": "bob"})
    assert out["result"]["isError"] is True
    assert "not available in this session's current state" in _text(out["result"])
    assert "send" not in _calls(log)


def test_an_admitted_tool_is_still_decided_with_its_arguments(env):
    svc, log = env
    gw = svc.gateway
    gw.call_tool("s1", "acme__check", {})
    out = gw.call_tool("s1", "acme__send", {"to": "evil"})
    assert out["result"]["isError"] is True and "evil" in _text(out["result"])
    assert _calls(log) == ["check"]


def test_credentials_come_from_the_daemon_not_the_agent(env):
    svc, _log = env
    assert "ACME_TOKEN" not in os.environ
    gw = svc.gateway
    gw.call_tool("s1", "acme__check", {})
    out = gw.call_tool("s1", "acme__send", {"to": "bob"})
    assert _text(out["result"]) == "token=s3cret"


def test_an_unknown_tool_is_an_error_result(env):
    svc, log = env
    out = svc.gateway.call_tool("s1", "acme__nope", {})
    assert out["result"]["isError"] is True and "no tool" in _text(out["result"])
    assert _calls(log) == []


def test_bad_params_are_refused(env):
    svc, _log = env
    with pytest.raises(ValueError):
        svc.gateway.list_tools("../etc")
    with pytest.raises(ValueError):
        svc.gateway.call_tool("s1", "acme__check", ["not", "an", "object"])


def test_an_admitted_call_is_in_the_sessions_trace(env):
    svc, _log = env
    svc.gateway.call_tool("s1", "acme__check", {})
    events = svc.traces.events("acme", "gateway:s1")
    assert [e.tool for e in events] == ["mcp__acme__check"]


def test_observe_mode_lists_everything(env, short_tmp):
    svc, _log = env
    svc._policy = GuardPolicy(mode="observe", on_error="deny")
    assert _names(svc.gateway.list_tools("s1")) == [
        "acme__check",
        "acme__send",
        "acme__wipe",
    ]


def test_unconfigured_deny_hides_a_server_without_a_library(env, short_tmp):
    svc, _log = env
    (short_tmp / "plugins" / "acme" / "sponsio.yaml").unlink()
    svc.store.invalidate()
    assert len(_names(svc.gateway.list_tools("s1"))) == 3
    svc._policy = GuardPolicy(mode="enforce", on_error="deny", unconfigured="deny")
    assert _names(svc.gateway.list_tools("s1")) == []


def test_a_server_removed_from_the_config_is_stopped(env, short_tmp):
    svc, _log = env
    gw = svc.gateway
    gw.list_tools("s1")
    (up,) = gw.upstreams().values()
    proc = up._proc
    assert proc is not None and proc.poll() is None
    time.sleep(0.01)  # a new mtime
    (short_tmp / "state" / "gateway.yaml").write_text("servers: {}\n")
    assert gw.list_tools("s1")["tools"] == []
    proc.wait(timeout=5)


def test_a_server_that_cannot_start_contributes_nothing(env, short_tmp):
    svc, _log = env
    time.sleep(0.01)
    (short_tmp / "state" / "gateway.yaml").write_text(
        json.dumps({"servers": {"acme": {"command": ["/nonexistent/bin"]}}})
    )
    reply = svc.gateway.list_tools("s1")
    assert reply["tools"] == [] and "cannot start" in reply["errors"]["acme"]


def test_an_upstream_that_exits_is_restarted(short_tmp):
    from sponsio.daemon.gateway import ServerSpec

    up = Upstream(ServerSpec(name="acme", command=(sys.executable, str(FAKE))))
    try:
        assert [t["name"] for t in up.tools()] == ["check", "send", "wipe"]
        up._proc.kill()
        up._proc.wait()
        assert up.call("check", {})["content"][0]["text"] == "check done"
    finally:
        up.close()


def test_a_silent_upstream_times_out(short_tmp):
    from sponsio.daemon.gateway import ServerSpec

    up = Upstream(
        ServerSpec(
            name="mute",
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_s=1.0,
        )
    )
    try:
        with pytest.raises(GatewayError, match="did not answer"):
            up.tools()
    finally:
        up.close()


# ---------------------------------------------------------------------------
# End to end: the shim as a host runs it
# ---------------------------------------------------------------------------


class Host:
    """Drives the gateway shim over stdio, as Claude Code would."""

    def __init__(self, socket: Path, *extra: str) -> None:
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                str(mcp_shim_path()),
                "--socket",
                str(socket),
                "--timeout",
                "20",
                *extra,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(socket.parent)},
        )
        self.next_id = 0
        self.notifications: list[str] = []

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            assert line, "the shim exited"
            reply = json.loads(line)
            if "method" in reply:
                self.notifications.append(reply["method"])
                continue
            assert reply["id"] == self.next_id
            return reply

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def daemon(env, short_tmp: Path):
    svc, log = env
    server = DaemonServer(short_tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        yield server.socket_path, log
    finally:
        server.stop(join_timeout_s=0.5)


def test_the_shim_imports_only_the_standard_library():
    src = mcp_shim_path().read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "sponsio" not in stripped or "sponsio_shim" in stripped, line


def test_a_host_session_through_the_shim(daemon):
    socket, log = daemon
    host = Host(Path(socket))
    try:
        init = host.request("initialize", {"protocolVersion": "2025-06-18"})
        assert init["result"]["protocolVersion"] == "2025-06-18"
        assert init["result"]["capabilities"]["tools"]["listChanged"] is True
        listed = host.request("tools/list")["result"]["tools"]
        assert [t["name"] for t in listed] == ["acme__check", "acme__wipe"]

        refused = host.request(
            "tools/call", {"name": "acme__send", "arguments": {"to": "bob"}}
        )
        assert refused["result"]["isError"] is True
        assert host.notifications == []

        host.request("tools/call", {"name": "acme__check", "arguments": {}})
        assert host.notifications == ["notifications/tools/list_changed"]
        listed = host.request("tools/list")["result"]["tools"]
        assert "acme__send" in [t["name"] for t in listed]
        sent = host.request(
            "tools/call", {"name": "acme__send", "arguments": {"to": "bob"}}
        )
        assert sent["result"]["content"][0]["text"] == "token=s3cret"
        assert _calls(log) == ["check", "send"]
    finally:
        host.close()


def test_no_daemon_means_no_tools_and_no_calls(short_tmp: Path):
    host = Host(short_tmp / "absent.sock", "--sponsio", "/usr/bin/false")
    try:
        listed = host.request("tools/list")
        assert listed["error"]["code"] == -32000
        assert "not reachable" in listed["error"]["message"]
        called = host.request("tools/call", {"name": "acme__wipe", "arguments": {}})
        assert called["result"]["isError"] is True
    finally:
        host.close()


def test_unknown_methods_and_bad_lines_get_json_rpc_errors(daemon):
    socket, _log = daemon
    host = Host(Path(socket))
    try:
        assert host.request("resources/list")["error"]["code"] == -32601
        assert host.request("ping")["result"] == {}
        host.proc.stdin.write("not json\n")
        host.proc.stdin.flush()
        bad = json.loads(host.proc.stdout.readline())
        assert bad["error"]["code"] == -32700
        host.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 99}) + "\n")
        host.proc.stdin.flush()
        bad = json.loads(host.proc.stdout.readline())
        assert (bad["id"], bad["error"]["code"]) == (99, -32600)
    finally:
        host.close()


def test_the_rpc_is_on_the_agent_socket(daemon):
    socket, _log = daemon
    client = DaemonClient(socket_path=Path(socket), timeout_s=10.0)
    reply = client.call("gateway.list_tools", {"session": "direct"})
    assert [t["name"] for t in reply["tools"]] == ["acme__check", "acme__wipe"]
