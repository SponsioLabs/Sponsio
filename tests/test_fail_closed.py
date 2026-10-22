"""Every error path on every host denies.

One table: for each entry point a hook can reach (the shim, the daemon's
RPC, the in-process ``--direct`` path, the legacy ``sponsio host guard``
command) and each host, every way a call can fail to be evaluated must
come back as that host's deny. Before P0.4, an unknown host name, empty
stdin, invalid JSON and a non-object payload were all allowed, and the
OpenClaw plugin let a call through when the daemon answered with an
error. ``SPONSIO_HOOK_ON_ERROR=allow`` still lets an evaluation error
through, and the daemon writes each such call to its audit log.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from sponsio.daemon import audit
from sponsio.daemon.guard_service import FAIL_OPEN, KNOWN_HOSTS, GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer, RpcError
from sponsio.guard_stdin import GuardPolicy, run_stdin
from sponsio.integrations import hosts
from sponsio.integrations.cursor import run_cursor_stdin
from sponsio.shim import sponsio_shim

HOSTS = ("claude-code", "cursor", "openclaw")
BUCKETS = {
    "claude-code": "_host_claude_code",
    "cursor": "_host_cursor",
    "openclaw": "_host_openclaw",
}
CURSOR_EVENT = "beforeShellExecution"

LIBRARY = """
version: "1"
agents:
  {agent}:
    contracts:
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
"""
BROKEN = "version: '1'\nagents: [this is: not, valid: yaml\n"


def _payload(host: str) -> dict:
    if host == "cursor":
        return {"command": "ls", "conversation_id": "c1"}
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "session_id": "s1",
    }


def _event(host: str) -> str | None:
    return CURSOR_EVENT if host == "cursor" else None


def assert_denied(host: str, stdout: str, code: int) -> None:
    """The host's deny: Cursor's ``permission: deny`` (with exit 2), or
    Claude Code's ``permissionDecision: deny`` (OpenClaw's hook path
    speaks the same protocol)."""
    body = json.loads(stdout)
    if host == "cursor":
        assert body.get("permission") == "deny", body
        assert code == 2
    else:
        assert body["hookSpecificOutput"]["permissionDecision"] == "deny", body


@pytest.fixture
def sandbox(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="spf-", dir="/tmp"))
    for name, root in (("good", d / "good"), ("broken", d / "broken")):
        for agent in BUCKETS.values():
            lib = root / agent / "sponsio.yaml"
            lib.parent.mkdir(parents=True)
            lib.write_text(BROKEN if name == "broken" else LIBRARY.format(agent=agent))
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    for var in ("SPONSIO_GUARD_MODE", "SPONSIO_HOOK_ON_ERROR", "SPONSIO_UNCONFIGURED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(d / "good"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _serve(sock: Path, register) -> DaemonServer:
    server = DaemonServer(sock, accept_timeout_s=0.05)
    register(server)
    server.start()
    return server


@pytest.fixture
def daemons(sandbox):
    """Four daemons on four sockets: one serving good libraries, one
    serving broken ones, one answering every check with an error, one
    answering with a reply that is not a result."""
    servers = {}

    def with_service(root: str):
        def register(server):
            svc = GuardService(root=sandbox / root, state_dir=sandbox / f"st-{root}")
            register_default_handlers(server, guard_service=svc)

        return register

    def failing(server):
        def boom(_params):
            raise RpcError("handler exploded", code="internal")

        server.register("guard.check", boom)
        server.register("guard.observe", boom)

    def garbled(server):
        server.register("guard.check", lambda _p: "not a result")
        server.register("guard.observe", lambda _p: "not a result")

    for name, reg in (
        ("good", with_service("good")),
        ("broken", with_service("broken")),
        ("error", failing),
        ("garbled", garbled),
    ):
        servers[name] = _serve(sandbox / f"{name}.sock", reg)
    yield {name: s.socket_path for name, s in servers.items()}
    for s in servers.values():
        s.stop()


# ---------------------------------------------------------------------------
# The shim
# ---------------------------------------------------------------------------


def _shim(host: str, sock: Path, stdin: str, deadline: float = 2.0):
    argv = [host, "--socket", str(sock), "--deadline", str(deadline)]
    argv += ["--sponsio", "/usr/bin/false"]  # nothing can start a daemon
    if host == "cursor":
        argv += ["--event", CURSOR_EVENT]
    out, code, _err = sponsio_shim.run(argv, stdin)
    return out, code


SHIM_CASES = {
    "empty stdin": ("good", lambda h: ""),
    "invalid JSON": ("good", lambda h: "{not json"),
    "not an object": ("good", lambda h: "[1, 2]"),
    "library does not load": ("broken", lambda h: json.dumps(_payload(h))),
    "daemon answers with an error": ("error", lambda h: json.dumps(_payload(h))),
    "daemon reply is garbled": ("garbled", lambda h: json.dumps(_payload(h))),
    "no daemon and none can start": (None, lambda h: json.dumps(_payload(h))),
}


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("case", list(SHIM_CASES))
def test_shim_denies(daemons, sandbox, host, case):
    which, stdin = SHIM_CASES[case]
    sock = daemons[which] if which else sandbox / "nobody.sock"
    out, code = _shim(host, sock, stdin(host), deadline=0.5 if not which else 2.0)
    assert_denied(host, out, code)


@pytest.mark.parametrize("host", ["vscode", ""])
def test_shim_denies_a_host_it_does_not_know(daemons, host):
    out, code = _shim(host, daemons["good"], json.dumps(_payload("claude-code")))
    assert code == 2
    assert_denied("claude-code", out, code)


def test_the_shim_allows_a_good_call(daemons):
    # The table is not vacuous: the same plumbing lets a clean call through.
    for host in HOSTS:
        out, code = _shim(host, daemons["good"], json.dumps(_payload(host)))
        assert code == 0, (host, out)


# ---------------------------------------------------------------------------
# The daemon's RPC
# ---------------------------------------------------------------------------


RPC_CASES = {
    "empty stdin": ("good", ""),
    "invalid JSON": ("good", "{not json"),
    "not an object": ("good", "[1, 2]"),
    "library does not load": ("broken", None),
}


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("case", list(RPC_CASES))
def test_daemon_denies(sandbox, host, case):
    root, raw = RPC_CASES[case]
    svc = GuardService(root=sandbox / root, state_dir=sandbox / "st")
    res = svc.check(
        host=host,
        hook_event=_event(host),
        raw=raw if raw is not None else json.dumps(_payload(host)),
    )
    assert res["allowed"] is False
    assert_denied(host, res["stdout"], res["exit_code"])


@pytest.mark.parametrize("host", ["vscode", ""])
def test_daemon_denies_a_host_it_does_not_know(sandbox, host):
    svc = GuardService(root=sandbox / "good", state_dir=sandbox / "st")
    res = svc.check(host=host, raw=json.dumps(_payload("claude-code")))
    assert res["allowed"] is False and res["exit_code"] == 2
    assert_denied("claude-code", res["stdout"], res["exit_code"])


def test_daemon_denies_a_cursor_event_it_cannot_convert(sandbox, monkeypatch):
    def boom(*_a, **_k):
        raise ValueError("unexpected shape")

    monkeypatch.setattr(
        "sponsio.integrations.cursor.cursor_event_to_sponsio_event", boom
    )
    svc = GuardService(root=sandbox / "good", state_dir=sandbox / "st")
    res = svc.check(host="cursor", hook_event=CURSOR_EVENT, payload=_payload("cursor"))
    assert res["allowed"] is False
    assert_denied("cursor", res["stdout"], res["exit_code"])


def test_every_fail_open_call_is_audited(sandbox):
    svc = GuardService(
        root=sandbox / "broken",
        state_dir=sandbox / "st",
        policy=GuardPolicy(on_error="allow"),
    )
    res = svc.check(host="claude-code", payload=_payload("claude-code"))
    assert res["allowed"] is True
    records = audit.read(sandbox / "st", FAIL_OPEN)
    assert len(records) == 1
    assert records[0]["host"] == "claude-code" and records[0]["tool"] == "Bash"
    assert "evaluation error" in records[0]["reason"]


def test_the_daemon_and_the_shim_know_the_same_hosts():
    assert set(sponsio_shim.KNOWN_HOSTS) == set(KNOWN_HOSTS)
    assert set(KNOWN_HOSTS) == {h.name for h in hosts.available()}


# ---------------------------------------------------------------------------
# The in-process path (``host guard --direct``)
# ---------------------------------------------------------------------------


def _direct(host: str, stdin: str, capsys) -> tuple[str, int]:
    if host == "cursor":
        code = run_cursor_stdin(CURSOR_EVENT, stdin)
    else:
        code = run_stdin(stdin, default_host=host)
    return capsys.readouterr().out, code


DIRECT_CASES = {
    "empty stdin": (False, lambda h: ""),
    "invalid JSON": (False, lambda h: "{not json"),
    "not an object": (False, lambda h: "[1, 2]"),
    "library does not load": (True, lambda h: json.dumps(_payload(h))),
}


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("case", list(DIRECT_CASES))
def test_direct_path_denies(sandbox, monkeypatch, capsys, host, case):
    broken, stdin = DIRECT_CASES[case]
    if broken:
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(sandbox / "broken"))
    out, code = _direct(host, stdin(host), capsys)
    assert_denied(host, out, code)


def test_direct_cursor_denies_an_event_it_cannot_convert(sandbox, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise ValueError("unexpected shape")

    monkeypatch.setattr(
        "sponsio.integrations.cursor.cursor_event_to_sponsio_event", boom
    )
    out, code = _direct("cursor", json.dumps(_payload("cursor")), capsys)
    assert_denied("cursor", out, code)


# ---------------------------------------------------------------------------
# The legacy ``sponsio host guard`` command
# ---------------------------------------------------------------------------


def _host_guard(args: list[str], stdin: str, monkeypatch, sock: Path):
    from click.testing import CliRunner

    from sponsio.cli import cli

    monkeypatch.setenv("SPONSIO_DAEMON_SOCKET", str(sock))
    return CliRunner().invoke(cli, ["host", "guard", *args], input=stdin)


def test_host_guard_denies_a_host_it_does_not_know(daemons, monkeypatch):
    r = _host_guard(
        ["vscode"], json.dumps(_payload("claude-code")), monkeypatch, daemons["good"]
    )
    assert r.exit_code == 2
    assert "denied" in r.output


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("which", ["error", "garbled", "broken"])
def test_host_guard_denies_when_it_gets_no_verdict(daemons, monkeypatch, host, which):
    args = [host] + (["--event", CURSOR_EVENT] if host == "cursor" else [])
    r = _host_guard(args, json.dumps(_payload(host)), monkeypatch, daemons[which])
    first = r.stdout.strip().splitlines()[0]
    assert_denied(host, first, r.exit_code)


@pytest.mark.parametrize("host", HOSTS)
def test_host_guard_denies_malformed_stdin(daemons, monkeypatch, host):
    args = [host] + (["--event", CURSOR_EVENT] if host == "cursor" else [])
    r = _host_guard(args, "{not json", monkeypatch, daemons["good"])
    first = r.stdout.strip().splitlines()[0]
    assert_denied(host, first, r.exit_code)
