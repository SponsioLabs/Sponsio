"""The daemon's hook evaluation: snapshot cache, in-memory traces, WAL,
per-conversation locking, and the ``guard.check`` / ``guard.observe``
RPCs end to end over a real Unix socket."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
from pathlib import Path

import pytest

from sponsio.daemon.client import DaemonClient
from sponsio.daemon.guard_service import GuardService, MemoryTraceStore, SnapshotStore
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer
from sponsio.guard_stdin import GuardPolicy

BUCKET = "_host_claude_code"
HOST = "claude-code"
KEY = "claude-code:c1"
"""The session key ``_bash()`` events land under (host-prefixed id)."""
WAL_NAME = "claude-code_c1.jsonl"


@pytest.fixture
def short_tmp() -> Path:
    """Short-path tempdir for AF_UNIX binding (~104 char cap on macOS)."""
    d = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))
    yield d
    for sub in sorted(d.rglob("*"), reverse=True):
        try:
            if sub.is_dir():
                sub.rmdir()
            else:
                sub.unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass


@pytest.fixture
def root(short_tmp: Path, monkeypatch) -> Path:
    r = short_tmp / "plugins"
    r.mkdir()
    # The service is given its root explicitly; the environment must
    # not matter, and the test proves it by pointing env elsewhere.
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(short_tmp / "elsewhere"))
    monkeypatch.delenv("SPONSIO_GUARD_MODE", raising=False)
    monkeypatch.delenv("SPONSIO_HOOK_ON_ERROR", raising=False)
    monkeypatch.delenv("SPONSIO_UNCONFIGURED", raising=False)
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    return r


def _write(root: Path, bucket: str, body: str) -> Path:
    d = root / bucket
    d.mkdir(parents=True, exist_ok=True)
    p = d / "sponsio.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _rate_limit(n: int, agent: str = BUCKET) -> str:
    return f"""
version: "1"
agents:
  {agent}:
    contracts:
      - desc: "at most {n} Bash calls"
        G:
          pattern: rate_limit
          args: [Bash, {n}]
"""


def _blacklist(agent: str = BUCKET) -> str:
    return f"""
version: "1"
agents:
  {agent}:
    contracts:
      - desc: "no recursive deletes of root"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf\\\\s+/"]]
"""


def _bash(command: str, conv: str | None = "c1", **extra) -> dict:
    ev = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        **extra,
    }
    if conv is not None:
        ev["conversation_id"] = conv
    return ev


def _service(root: Path, short_tmp: Path, **kw) -> GuardService:
    return GuardService(root=root, state_dir=short_tmp / "state", **kw)


# ---------------------------------------------------------------------------
# Snapshot cache
# ---------------------------------------------------------------------------


def test_snapshot_is_reused_until_content_changes(root: Path):
    lib = _write(root, BUCKET, _blacklist())
    store = SnapshotStore(root)
    first = store.resolve(BUCKET)
    assert first is not None and first.error is None
    assert store.resolve(BUCKET) is first
    # A touch with identical bytes keeps the snapshot.
    os.utime(lib, (time.time() + 5, time.time() + 5))
    assert store.resolve(BUCKET) is first
    # A content change produces a new one.
    lib.write_text(_rate_limit(1), encoding="utf-8")
    second = store.resolve(BUCKET)
    assert second is not first
    assert second.content_hash != first.content_hash


def test_snapshot_invalidates_when_an_included_pack_changes(
    root: Path, short_tmp: Path
):
    """The cache key covers the include closure, not just the yaml."""
    pack = root / BUCKET / "local-pack.yaml"
    pack.parent.mkdir(parents=True)
    pack.write_text(
        """
version: "1"
agents:
  "*":
    contracts:
      - desc: "pack rule"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf\\\\s+/"]]
""",
        encoding="utf-8",
    )
    _write(
        root,
        BUCKET,
        f"""
version: "1"
agents:
  {BUCKET}:
    include:
      - local-pack.yaml
""",
    )
    svc = _service(root, short_tmp)
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    assert svc.check(host=HOST, payload=_bash("rm -rf /"))["allowed"] is False
    # Only the pack changes; the including yaml is untouched.
    pack.write_text(
        pack.read_text().replace('"rm\\\\s+-rf\\\\s+/"', '"^ls$"'), encoding="utf-8"
    )
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is False
    assert svc.check(host=HOST, payload=_bash("rm -rf /"))["allowed"] is True


def test_on_change_policy_can_pin_the_served_snapshot(root: Path, short_tmp: Path):
    """The reload policy is replaceable without touching the interface."""
    lib = _write(root, BUCKET, _blacklist())
    svc = _service(root, short_tmp, on_change=lambda old, new: old or new)
    assert svc.check(host=HOST, payload=_bash("rm -rf /"))["allowed"] is False
    lib.write_text(_rate_limit(50), encoding="utf-8")
    # Pinned: the old snapshot keeps enforcing; the new one is visible as loaded.
    assert svc.check(host=HOST, payload=_bash("rm -rf /"))["allowed"] is False
    loaded = svc.store.loaded(BUCKET)
    served = svc.store.resolve(BUCKET)
    assert loaded is not None and served is not None
    assert loaded.content_hash != served.content_hash


def test_broken_library_denies_through_the_service(root: Path, short_tmp: Path):
    _write(
        root, BUCKET, "version: '1'\nagents:\n  _host_claude_code:\n    contracts: [\n"
    )
    svc = _service(root, short_tmp)
    res = svc.check(host=HOST, payload=_bash("ls"))
    assert res["allowed"] is False
    assert "SPONSIO_HOOK_ON_ERROR" in res["reason"]


def test_root_comes_from_the_service_not_the_environment(root: Path, short_tmp: Path):
    """The env points at a root with no library; the service's own root
    has one, and that is what decides."""
    _write(root, BUCKET, _blacklist())
    svc = _service(root, short_tmp)
    assert svc.check(host=HOST, payload=_bash("rm -rf /"))["allowed"] is False


# ---------------------------------------------------------------------------
# Traces: memory, isolation, WAL
# ---------------------------------------------------------------------------


def test_blocked_events_are_not_appended(root: Path, short_tmp: Path):
    _write(
        root,
        BUCKET,
        f"""
version: "1"
agents:
  {BUCKET}:
    contracts:
      - desc: "no recursive deletes of root"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf\\\\s+/"]]
      - desc: "at most two Bash calls"
        G:
          pattern: rate_limit
          args: [Bash, 2]
""",
    )
    svc = _service(root, short_tmp)
    assert svc.check(host=HOST, payload=_bash("rm -rf /"))["allowed"] is False
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is False
    assert len(svc.traces.events(BUCKET, KEY)) == 2


def test_conversations_are_isolated(root: Path, short_tmp: Path):
    _write(root, BUCKET, _rate_limit(1))
    svc = _service(root, short_tmp)
    assert svc.check(host=HOST, payload=_bash("ls", conv="a"))["allowed"] is True
    assert svc.check(host=HOST, payload=_bash("ls", conv="a"))["allowed"] is False
    assert svc.check(host=HOST, payload=_bash("ls", conv="b"))["allowed"] is True


def test_post_event_attaches_output_without_counting(root: Path, short_tmp: Path):
    _write(root, BUCKET, _rate_limit(2))
    svc = _service(root, short_tmp)
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    post = {**_bash("ls"), "hook_event_name": "PostToolUse", "tool_response": "a.txt"}
    res = svc.observe(host=HOST, payload=post)
    assert res["allowed"] is True and res["stdout"] == ""
    events = svc.traces.events(BUCKET, KEY)
    assert [e.tool for e in events] == ["Bash"]
    assert events[0].content == "a.txt"
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is False


def test_wal_restores_traces_across_two_restarts(root: Path, short_tmp: Path):
    _write(root, BUCKET, _rate_limit(2))
    svc1 = _service(root, short_tmp)
    assert svc1.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    post = {**_bash("ls"), "hook_event_name": "PostToolUse", "tool_response": "out"}
    svc1.observe(host=HOST, payload=post)
    del svc1
    svc2 = _service(root, short_tmp)
    assert svc2.traces.events(BUCKET, KEY)[0].content == "out"
    assert svc2.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    del svc2
    svc3 = _service(root, short_tmp)
    assert len(svc3.traces.events(BUCKET, KEY)) == 2
    assert svc3.check(host=HOST, payload=_bash("ls"))["allowed"] is False


def test_wal_records_carry_the_legacy_event_fields_and_seq(root: Path, short_tmp: Path):
    _write(root, BUCKET, _rate_limit(5))
    svc = _service(root, short_tmp)
    svc.check(host=HOST, payload=_bash("ls"))
    svc.observe(
        host=HOST,
        payload={**_bash("ls"), "hook_event_name": "PostToolUse", "tool_response": "x"},
    )
    wal = short_tmp / "state" / "wal" / WAL_NAME
    lines = [json.loads(line) for line in wal.read_text().splitlines()]
    assert set(lines[0]) == {"b", "e"}
    assert set(lines[0]["e"]) == {"ts", "agent", "type", "tool", "args", "seq"}
    assert lines[0]["e"]["seq"] == 0
    assert set(lines[1]) == {"b", "a"}
    assert set(lines[1]["a"]) == {"tool", "content"}


def test_wal_files_are_private(root: Path, short_tmp: Path):
    _write(root, BUCKET, _rate_limit(5))
    svc = _service(root, short_tmp)
    svc.check(host=HOST, payload=_bash("ls"))
    wal_dir = short_tmp / "state" / "wal"
    assert stat.S_IMODE(wal_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((wal_dir / WAL_NAME).stat().st_mode) == 0o600


def test_expired_conversation_is_dropped_with_its_log(root: Path, short_tmp: Path):
    _write(root, BUCKET, _rate_limit(1))
    policy = GuardPolicy(trace_ttl_hours=0.2 / 3600)  # 0.2 s
    svc = _service(root, short_tmp, policy=policy)
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is False
    time.sleep(0.3)
    assert svc.check(host=HOST, payload=_bash("ls"))["allowed"] is True
    assert svc.traces.conversations() == [WAL_NAME[:-6]]
    # And an expired log is not replayed on start.
    time.sleep(0.3)
    store = MemoryTraceStore(short_tmp / "state" / "wal", ttl_seconds=0.2)
    assert store.load() == 0
    assert not (short_tmp / "state" / "wal" / WAL_NAME).exists()


def test_concurrent_checks_respect_the_budget(root: Path, short_tmp: Path):
    """Eight callers race on one conversation with a budget of three;
    exactly three are admitted."""
    _write(root, BUCKET, _rate_limit(3))
    svc = _service(root, short_tmp)
    results: list[bool] = []
    lock = threading.Lock()

    def one():
        r = svc.check(host=HOST, payload=_bash("ls"))
        with lock:
            results.append(r["allowed"])

    threads = [threading.Thread(target=one) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 3
    assert len(svc.traces.events(BUCKET, KEY)) == 3


def test_policy_mode_observe_does_not_block(root: Path, short_tmp: Path):
    _write(root, BUCKET, _blacklist())
    svc = _service(root, short_tmp, policy=GuardPolicy(mode="observe"))
    res = svc.check(host=HOST, payload=_bash("rm -rf /"))
    assert res["allowed"] is True and res["stdout"] == ""


# ---------------------------------------------------------------------------
# RPC round trips
# ---------------------------------------------------------------------------


@pytest.fixture
def rpc(root: Path, short_tmp: Path):
    svc = _service(root, short_tmp)
    server = DaemonServer(short_tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        yield DaemonClient(socket_path=server.socket_path, timeout_s=5.0), svc
    finally:
        server.stop(join_timeout_s=0.5)


def test_rpc_check_renders_claude_code_deny(root: Path, rpc):
    client, _ = rpc
    _write(root, BUCKET, _blacklist())
    res = client.call("guard.check", {"host": HOST, "payload": _bash("rm -rf /")})
    assert res["allowed"] is False and res["exit_code"] == 0
    reply = json.loads(res["stdout"])
    assert reply["hookSpecificOutput"]["permissionDecision"] == "deny"
    ok = client.call("guard.check", {"host": HOST, "payload": _bash("ls")})
    assert ok["allowed"] is True and ok["stdout"] == ""


def test_rpc_check_accepts_raw_stdin(root: Path, rpc):
    client, _ = rpc
    _write(root, BUCKET, _blacklist())
    res = client.call(
        "guard.check", {"host": HOST, "raw": json.dumps(_bash("rm -rf /"))}
    )
    assert res["allowed"] is False
    empty = client.call("guard.check", {"host": HOST, "raw": "   \n"})
    assert empty["allowed"] is False and empty["exit_code"] == 2


def test_rpc_check_renders_cursor_deny(root: Path, rpc):
    client, _ = rpc
    _write(root, "_host_cursor", _blacklist("_host_cursor"))
    payload = {"command": "rm -rf /", "conversation_id": "cur-1"}
    res = client.call(
        "guard.check",
        {"host": "cursor", "event": "beforeShellExecution", "payload": payload},
    )
    assert res["allowed"] is False and res["exit_code"] == 2
    assert json.loads(res["stdout"])["permission"] == "deny"
    ok = client.call(
        "guard.check",
        {
            "host": "cursor",
            "event": "beforeShellExecution",
            "payload": {"command": "ls", "conversation_id": "cur-1"},
        },
    )
    assert ok["allowed"] is True
    assert json.loads(ok["stdout"])["permission"] == "allow"


def test_rpc_rejects_malformed_params(rpc):
    client, _ = rpc
    from sponsio.daemon.client import DaemonError

    with pytest.raises(DaemonError) as e:
        client.call("guard.check", {"host": HOST})
    assert e.value.code == "validation"
    with pytest.raises(DaemonError) as e:
        client.call("guard.check", {"host": HOST, "payload": "nope"})
    assert e.value.code == "validation"


def test_daemon_without_guard_service_has_no_guard_methods(short_tmp: Path):
    from sponsio.daemon.client import DaemonError

    server = DaemonServer(short_tmp / "plain.sock", accept_timeout_s=0.05)
    register_default_handlers(server)
    server.start()
    try:
        client = DaemonClient(socket_path=server.socket_path, timeout_s=5.0)
        with pytest.raises(DaemonError) as e:
            client.call("guard.check", {"host": HOST, "payload": _bash("ls")})
        assert e.value.code == "not_found"
    finally:
        server.stop(join_timeout_s=0.5)


# ---------------------------------------------------------------------------
# Session keys
# ---------------------------------------------------------------------------


def _claude(command: str, session: str | None, **extra) -> dict:
    ev = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        **extra,
    }
    if session is not None:
        ev["session_id"] = session
    return ev


def test_each_claude_code_session_counts_on_its_own(root: Path, short_tmp: Path):
    """Claude Code sends ``session_id`` and no ``conversation_id``; every
    session used to share one history, so a rate limit counted across
    all of them."""
    _write(root, BUCKET, _rate_limit(2))
    svc = _service(root, short_tmp)
    for _ in range(2):
        assert svc.check(host=HOST, payload=_claude("ls", "s1"))["allowed"] is True
    assert svc.check(host=HOST, payload=_claude("ls", "s1"))["allowed"] is False
    # A second session starts from zero.
    assert svc.check(host=HOST, payload=_claude("ls", "s2"))["allowed"] is True
    assert len(svc.traces.events(BUCKET, "claude-code:s1")) == 2
    assert len(svc.traces.events(BUCKET, "claude-code:s2")) == 1


def test_a_subagent_shares_its_parents_session(root: Path, short_tmp: Path):
    _write(root, BUCKET, _rate_limit(2))
    _write(
        root, "_host_claude_code_subagent", _rate_limit(2, "_host_claude_code_subagent")
    )
    svc = _service(root, short_tmp)
    assert svc.check(host=HOST, payload=_claude("ls", "s1"))["allowed"] is True
    sub = _claude("ls", "s1", agent_id="sub-1")
    assert svc.check(host=HOST, payload=sub)["allowed"] is True
    # Parent and sub-agent calls are one session's history.
    assert svc.traces.conversations() == ["claude-code_s1"]
    assert len(svc.traces.events(BUCKET, "claude-code:s1")) == 2


def test_conversation_id_wins_over_session_id_and_hosts_do_not_collide(
    root: Path, short_tmp: Path
):
    from sponsio.guard_stdin import conversation_key

    assert (
        conversation_key({"host": "cursor", "conversation_id": "x", "session_id": "y"})
        == "cursor:x"
    )
    assert (
        conversation_key({"host": "claude-code", "session_id": "x"}) == "claude-code:x"
    )
    assert conversation_key({"host": "openclaw", "session_id": " x "}) == "openclaw:x"
    assert conversation_key({"session_id": "x"}) == "_:x"
    assert conversation_key({"session_id": "  "}) is None
    assert conversation_key({}) is None

    _write(root, BUCKET, _rate_limit(1))
    _write(root, "_host_openclaw", _rate_limit(1, "_host_openclaw"))
    svc = _service(root, short_tmp)
    assert svc.check(host=HOST, payload=_claude("ls", "same"))["allowed"] is True
    # Same id from another host is another session.
    assert svc.check(host="openclaw", payload=_claude("ls", "same"))["allowed"] is True
    assert sorted(svc.traces.conversations()) == ["claude-code_same", "openclaw_same"]
