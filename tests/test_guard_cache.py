"""Reusing a live guard per session decides exactly as rebuilding one.

The daemon keeps one guard per (bucket, library content, session, mode)
and grounds only the new event on each check, instead of replaying the
whole session. These tests run the same random sequence of hook events
through two services, one reusing guards and one rebuilding every time,
and require the same verdict at every step; then they check that the
per-check cost no longer grows with the session.
"""

from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path

import pytest

from sponsio.daemon.guard_service import GuardService

MAIN = "_host_claude_code"
SUB = "_host_claude_code_subagent"
HOST = "claude-code"


def _library(agent: str) -> str:
    return f"""
version: "1"
agents:
  {agent}:
    contracts:
      - desc: "at most 6 Bash calls"
        G:
          pattern: rate_limit
          args: [Bash, 6]
      - desc: "check before deploy"
        G:
          pattern: must_precede
          args: [check, deploy]
      - desc: "deploy cools down"
        G:
          pattern: cooldown
          args: [deploy, 2]
      - desc: "no long Read loops"
        G:
          pattern: loop_detection
          args: [Read, 3]
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
      - desc: "no Write once a Read showed a secret"
        G:
          ltl: "G(output_has('Read', 'SECRET') -> G(!(called('Write'))))"
"""


@pytest.fixture
def root():
    d = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))
    for agent in (MAIN, SUB):
        lib = d / "plugins" / agent / "sponsio.yaml"
        lib.parent.mkdir(parents=True)
        lib.write_text(_library(agent))
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


def _services(root: Path, monkeypatch):
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    monkeypatch.delenv("SPONSIO_GUARD_MODE", raising=False)
    reuse = GuardService(root=root / "plugins", state_dir=root / "a")
    rebuild = GuardService(
        root=root / "plugins", state_dir=root / "b", reuse_guards=False
    )
    return reuse, rebuild


_TOOLS = [
    ("Bash", lambda r: {"command": r.choice(["ls", "pwd", "rm -rf /tmp/x"])}),
    ("Read", lambda r: {"file_path": r.choice(["/a", "/b"])}),
    ("check", lambda r: {}),
    ("deploy", lambda r: {"target": "prod"}),
    ("Write", lambda r: {"file_path": "/out", "content": "x"}),
]


def _random_event(r: random.Random) -> dict:
    session = r.choice(["s1", "s1", "s1", "s2"])
    if r.random() < 0.3:
        tool = r.choice(["Read", "Bash", "Write"])
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {},
            "tool_response": r.choice(["ok", "SECRET=1", "nothing"]),
            "session_id": session,
        }
    tool, args = r.choice(_TOOLS)
    ev = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": args(r) or {"_": 1},
        "session_id": session,
    }
    if r.random() < 0.25:
        ev["agent_id"] = "sub-1"
    if r.random() < 0.05:
        ev["context"] = {"tenant": r.choice(["a", "b"])}
    return ev


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_reused_guards_decide_like_rebuilt_ones(root, monkeypatch, seed):
    reuse, rebuild = _services(root, monkeypatch)
    r = random.Random(seed)
    for step in range(300):
        ev = _random_event(r)
        method = "observe" if ev["hook_event_name"] == "PostToolUse" else "check"
        a = getattr(reuse, method)(host=HOST, payload=dict(ev))
        b = getattr(rebuild, method)(host=HOST, payload=dict(ev))
        assert (a["allowed"], a["reason"], a["stdout"]) == (
            b["allowed"],
            b["reason"],
            b["stdout"],
        ), f"seed {seed} step {step}: {ev}"
    assert reuse.guards is not None and reuse.guards.hits > 100
    # Both services recorded the same history.
    for sid in ("claude-code:s1", "claude-code:s2"):
        ea = [(e.ts, e.tool, e.content) for e in reuse.traces.events(MAIN, sid)]
        eb = [(e.ts, e.tool, e.content) for e in rebuild.traces.events(MAIN, sid)]
        assert ea == eb


def test_a_library_edit_starts_a_new_guard(root, monkeypatch):
    reuse, _ = _services(root, monkeypatch)
    ev = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "session_id": "s",
    }
    assert reuse.check(host=HOST, payload=ev)["allowed"]
    assert reuse.check(host=HOST, payload=ev)["allowed"]
    hits = reuse.guards.hits
    lib = root / "plugins" / MAIN / "sponsio.yaml"
    lib.write_text(lib.read_text().replace("[Bash, 6]", "[Bash, 2]"))
    # New content: a new guard, built from the session's history (two
    # admitted calls), so the tighter limit applies at once.
    assert reuse.check(host=HOST, payload=ev)["allowed"] is False
    assert reuse.guards.hits == hits


def test_an_expired_session_is_not_resumed_from_a_stale_guard(root, monkeypatch):
    reuse, _ = _services(root, monkeypatch)
    reuse.traces._ttl = 0.2
    ev = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "session_id": "s",
    }
    for _ in range(6):
        assert reuse.check(host=HOST, payload=ev)["allowed"]
    assert reuse.check(host=HOST, payload=ev)["allowed"] is False
    time.sleep(0.3)
    # The session expired: its history is gone, and so is the guard's view.
    assert reuse.check(host=HOST, payload=ev)["allowed"] is True


def test_check_cost_does_not_grow_with_the_session(tmp_path, monkeypatch):
    """With the shipped host library: a 400-event session's check costs
    within a small factor of a 10-event one's, and a fraction of what
    rebuilding the guard costs at that length. (What growth is left is
    the verifier's own evaluation of reactive contracts, not replay.)"""
    import shutil

    import sponsio

    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    lib = tmp_path / "plugins" / MAIN / "sponsio.yaml"
    lib.parent.mkdir(parents=True)
    shutil.copy(
        Path(sponsio.__file__).parent / "plugin" / "defaults" / f"{MAIN}.yaml", lib
    )

    def pre(i: int, session: str) -> dict:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": f"/f{i}"},
            "session_id": session,
        }

    def post(i: int, session: str) -> dict:
        return {
            **pre(i, session),
            "hook_event_name": "PostToolUse",
            "tool_response": "x",
        }

    def grow(svc: GuardService, session: str, n: int) -> None:
        for i in range(n):
            svc.check(host=HOST, payload=pre(i, session))
            svc.observe(host=HOST, payload=post(i, session))

    def p50(svc: GuardService, session: str, start: int) -> float:
        samples = []
        for i in range(15):
            t = time.perf_counter()
            svc.check(host=HOST, payload=pre(start + i, session))
            samples.append(time.perf_counter() - t)
            svc.observe(host=HOST, payload=post(start + i, session))
        return sorted(samples)[len(samples) // 2]

    reuse = GuardService(root=tmp_path / "plugins", state_dir=tmp_path / "a")
    grow(reuse, "short", 10)
    grow(reuse, "long", 400)
    short = p50(reuse, "short", 10)
    long_ = p50(reuse, "long", 400)
    # The same 415-event session, restored from the log into a service
    # that rebuilds the guard on every check.
    rebuild = GuardService(
        root=tmp_path / "plugins", state_dir=tmp_path / "a", reuse_guards=False
    )
    rebuilt_long = p50(rebuild, "long", 1000)
    assert long_ < short * 5, (short, long_)
    assert long_ < rebuilt_long / 5, (long_, rebuilt_long)
    assert reuse.guards.misses <= 2
