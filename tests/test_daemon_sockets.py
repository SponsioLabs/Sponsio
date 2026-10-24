"""Two sockets, kernel-verified peers, and proposals instead of writes.

The agent socket serves what a hook or an agent-side client may call;
the admin socket serves everything that changes state, and audits every
call with the caller's uid, gid and pid as the kernel reports them. A
method on the wrong socket does not exist there. Rule changes from the
agent side are proposals, applied only by an admin approval.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import tempfile
from pathlib import Path

import pytest

from sponsio.daemon import audit
from sponsio.daemon.client import DaemonClient, DaemonError
from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.peercred import get_peer
from sponsio.daemon.server import DaemonServer, admin_socket_for, current_peer

BUCKET = "_host_claude_code"
LIBRARY = """
version: "1"
agents:
  _host_claude_code:
    contracts:
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
"""
STAGING = """
agents:
  _host_claude_code:
    contracts:
      - desc: "at most 3 Bash calls"
        G: { pattern: rate_limit, args: [Bash, 3] }
"""


@pytest.fixture
def tmp(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="sps-", dir="/tmp"))
    lib = d / "plugins" / BUCKET / "sponsio.yaml"
    lib.parent.mkdir(parents=True)
    lib.write_text(LIBRARY)
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    monkeypatch.delenv("SPONSIO_GUARD_MODE", raising=False)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def daemon(tmp):
    svc = GuardService(root=tmp / "plugins", state_dir=tmp / "state")
    server = DaemonServer(tmp / "sock", accept_timeout_s=0.05, socket_mode=0o666)
    register_default_handlers(server, guard_service=svc)
    server.register("whoami", lambda _p: current_peer().as_dict())
    server.register("whoami", lambda _p: current_peer().as_dict(), admin=True)
    server.start()
    yield server
    server.stop(join_timeout_s=0.5)


def _agent(server):
    return DaemonClient(socket_path=server.socket_path, timeout_s=5.0)


def _admin(server):
    return DaemonClient(socket_path=server.admin_socket_path, timeout_s=5.0)


def _bash(command: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "s1",
    }


# ---------------------------------------------------------------------------
# Peer identity
# ---------------------------------------------------------------------------


def test_the_kernel_names_the_peer():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        peer = get_peer(a)
    finally:
        a.close()
        b.close()
    assert (peer.uid, peer.gid, peer.pid) == (os.getuid(), os.getegid(), os.getpid())


def test_every_request_is_tagged_with_its_peer(daemon):
    for client in (_agent(daemon), _admin(daemon)):
        who = client.call("whoami")
        assert who == {"uid": os.getuid(), "gid": os.getegid(), "pid": os.getpid()}


def test_admin_socket_sits_beside_the_agent_socket():
    assert admin_socket_for("/x/sponsio.sock") == Path("/x/sponsio.admin.sock")
    assert admin_socket_for("/x/sock") == Path("/x/sock.admin")


def test_the_admin_socket_is_owner_only(daemon):
    assert stat.S_IMODE(daemon.socket_path.stat().st_mode) == 0o666
    assert stat.S_IMODE(daemon.admin_socket_path.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# Method tables
# ---------------------------------------------------------------------------

AGENT_ONLY = [
    "guard.check",
    "guard.observe",
    "gateway.list_tools",
    "gateway.call_tool",
    "sdk.check",
    "sdk.observe",
    "sdk.filter_tools",
    "proposal.submit",
]
ADMIN_ONLY = [
    "plugin.append",
    "mode.set",
    "proposal.list",
    "proposal.show",
    "proposal.approve",
    "proposal.reject",
    "audit.verify",
    "cloud.set_key",
    "cloud.key_status",
]


def test_the_tables_are_what_the_contract_says(daemon):
    assert set(AGENT_ONLY) <= set(daemon.methods())
    assert set(ADMIN_ONLY) <= set(daemon.methods(admin=True))
    assert not set(ADMIN_ONLY) & set(daemon.methods())
    assert not set(AGENT_ONLY) & set(daemon.methods(admin=True))


@pytest.mark.parametrize("method", ADMIN_ONLY)
def test_an_admin_method_on_the_agent_socket_is_not_found(daemon, method):
    with pytest.raises(DaemonError) as e:
        _agent(daemon).call(method, {})
    assert e.value.code == "not_found"


@pytest.mark.parametrize("method", AGENT_ONLY)
def test_an_agent_method_on_the_admin_socket_is_not_found(daemon, method):
    with pytest.raises(DaemonError) as e:
        _admin(daemon).call(method, {"host": "claude-code", "payload": _bash("ls")})
    assert e.value.code == "not_found"


# ---------------------------------------------------------------------------
# Admin policy and audit
# ---------------------------------------------------------------------------


def test_admin_calls_are_audited_with_the_peer(daemon, tmp):
    _admin(daemon).call("mode.set", {"host": "claude-code", "mode": "observe"})
    (rec,) = audit.read(tmp / "state", "admin_call")
    assert rec["method"] == "mode.set" and rec["ok"] is True
    assert rec["peer"] == {"uid": os.getuid(), "gid": os.getegid(), "pid": os.getpid()}
    # The call's parameters are fingerprinted, not copied: a staging
    # document can be long, and the audit log is not a rule store.
    assert "params" not in rec and len(rec["params_sha256"]) == 64


def test_a_peer_the_policy_refuses_is_refused_and_audited(tmp):
    svc = GuardService(root=tmp / "plugins", state_dir=tmp / "state")
    server = DaemonServer(
        tmp / "sock", accept_timeout_s=0.05, admin_policy=lambda peer: False
    )
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        with pytest.raises(DaemonError) as e:
            _admin(server).call("mode.set", {"host": "claude-code", "mode": "observe"})
        assert e.value.code == "permission"
    finally:
        server.stop(join_timeout_s=0.5)
    (rec,) = audit.read(tmp / "state", "admin_call")
    assert rec["ok"] is False and rec["code"] == "permission"
    assert rec["peer"]["uid"] == os.getuid()


def test_mode_set_changes_how_calls_without_a_mode_are_decided(daemon):
    agent = _agent(daemon)
    check = {"host": "claude-code", "payload": _bash("rm -rf /tmp/x")}
    assert agent.call("guard.check", check)["allowed"] is False
    _admin(daemon).call("mode.set", {"host": "claude-code", "mode": "observe"})
    assert agent.call("guard.check", check)["allowed"] is True


@pytest.mark.parametrize(
    "params",
    [{"host": "vscode", "mode": "observe"}, {"host": "claude-code", "mode": "off"}],
)
def test_mode_set_validates(daemon, params):
    with pytest.raises(DaemonError) as e:
        _admin(daemon).call("mode.set", params)
    assert e.value.code == "validation"


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


def _submit(daemon, tmp, **extra):
    params = {
        "target": BUCKET,
        "staging_yaml": STAGING,
        "root": str(tmp / "plugins"),
    }
    return _agent(daemon).call(
        "proposal.submit", {"kind": "plugin.append", "params": params, **extra}
    )


def test_a_proposal_changes_nothing_until_approved(daemon, tmp):
    lib = tmp / "plugins" / BUCKET / "sponsio.yaml"
    before = lib.read_text()
    p = _submit(daemon, tmp)
    assert p["status"] == "pending" and p["preview"]["appended_count"] == 1
    assert lib.read_text() == before
    assert p["submitted_by"]["uid"] == os.getuid()

    listed = _admin(daemon).call("proposal.list", {"status": "pending"})
    assert [x["id"] for x in listed["proposals"]] == [p["id"]]

    done = _admin(daemon).call("proposal.approve", {"id": p["id"]})
    assert done["status"] == "approved" and done["decided_by"]["uid"] == os.getuid()
    assert "at most 3 Bash calls" in lib.read_text()
    assert [r["id"] for r in audit.read(tmp / "state", "proposal_approved")] == [
        p["id"]
    ]

    with pytest.raises(DaemonError) as e:
        _admin(daemon).call("proposal.approve", {"id": p["id"]})
    assert e.value.code == "conflict"


def test_a_rejected_proposal_is_never_applied(daemon, tmp):
    lib = tmp / "plugins" / BUCKET / "sponsio.yaml"
    before = lib.read_text()
    p = _submit(daemon, tmp)
    assert (
        _admin(daemon).call("proposal.reject", {"id": p["id"]})["status"] == "rejected"
    )
    assert lib.read_text() == before
    with pytest.raises(DaemonError):
        _admin(daemon).call("proposal.approve", {"id": p["id"]})


def test_an_invalid_proposal_is_refused_at_submit(daemon, tmp):
    bad = {
        "target": BUCKET,
        "staging_yaml": "customized: {}\n",
        "root": str(tmp / "plugins"),
    }
    with pytest.raises(DaemonError) as e:
        _agent(daemon).call("proposal.submit", {"kind": "plugin.append", "params": bad})
    assert e.value.code == "validation"
    assert _admin(daemon).call("proposal.list", {})["proposals"] == []


def test_a_dry_run_proposal_is_not_stored(daemon, tmp):
    p = _submit(daemon, tmp, dry_run=True)
    assert p["id"] is None and p["preview"]["appended_count"] == 1
    assert _admin(daemon).call("proposal.list", {})["proposals"] == []


def test_only_known_kinds_can_be_proposed(daemon):
    with pytest.raises(DaemonError) as e:
        _agent(daemon).call("proposal.submit", {"kind": "mode.set", "params": {}})
    assert e.value.code == "validation"


def test_proposal_files_are_private(daemon, tmp):
    p = _submit(daemon, tmp)
    path = tmp / "state" / "proposals" / f"{p['id']}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["kind"] == "plugin.append"


def test_a_proposal_writes_only_to_the_daemons_rule_store(daemon, tmp):
    elsewhere = tmp / "elsewhere"
    (elsewhere / BUCKET).mkdir(parents=True)
    (elsewhere / BUCKET / "sponsio.yaml").write_text(LIBRARY)
    params = {"target": BUCKET, "staging_yaml": STAGING, "root": str(elsewhere)}
    p = _agent(daemon).call(
        "proposal.submit", {"kind": "plugin.append", "params": params}
    )
    _admin(daemon).call("proposal.approve", {"id": p["id"]})
    assert (elsewhere / BUCKET / "sponsio.yaml").read_text() == LIBRARY
    assert (
        "at most 3 Bash calls"
        in (tmp / "plugins" / BUCKET / "sponsio.yaml").read_text()
    )
