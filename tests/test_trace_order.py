"""A rebuilt trace is in the order events were admitted.

Contracts are LTLf over the trace, so order is semantics: ``cooldown``
counts positions. The daemon used to rebuild a session's history by
``(ts, agent)``, but a guard's ``ts`` is its trace length, which counts a
``context_update`` the store never records, so two events could share a
``ts`` and come back in the wrong order. The store now numbers each
conversation's events as it admits them (``seq``), logs the number, and
rebuilds by it.
"""

from __future__ import annotations

import json
import random
import shutil
import tempfile
from pathlib import Path

import pytest

from sponsio.daemon import guard_service
from sponsio.daemon.guard_service import GuardService, MemoryTraceStore
from tests.test_guard_cache import HOST, MAIN, SUB, _library, _random_event

COUNTEREXAMPLE_SEED = 4
COUNTEREXAMPLE_STEP = 59
"""With ``_random_event`` and this seed, the deploy at this step is
allowed in admission order and was blocked by ``cooldown`` when the
history was ordered by ``(ts, agent)``."""


class _AdmissionOrder(MemoryTraceStore):
    """The oracle: history exactly as appended, numbered by position."""

    def records(self, conversation_id):
        with self._lock:
            conv = self._conv(conversation_id, create=False)
            if conv is None:
                return []
            raw = [dict(d) for _b, d in conv.events]
        out = []
        for i, d in enumerate(raw):
            d["seq"] = i
            out.append((i, guard_service._to_event(d)))
        return out


@pytest.fixture
def root(monkeypatch):
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    monkeypatch.delenv("SPONSIO_GUARD_MODE", raising=False)
    d = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))
    for agent in (MAIN, SUB):
        lib = d / "plugins" / agent / "sponsio.yaml"
        lib.parent.mkdir(parents=True)
        lib.write_text(_library(agent))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _call(svc: GuardService, ev: dict) -> dict:
    method = "observe" if ev["hook_event_name"] == "PostToolUse" else "check"
    return getattr(svc, method)(host=HOST, payload=dict(ev))


@pytest.mark.parametrize("reuse_guards", [True, False])
def test_the_cooldown_counterexample_decides_in_admission_order(root, reuse_guards):
    oracle = GuardService(
        root=root / "plugins", state_dir=root / "oracle", reuse_guards=False
    )
    oracle.traces.__class__ = _AdmissionOrder

    def service() -> GuardService:
        return GuardService(
            root=root / "plugins", state_dir=root / "svc", reuse_guards=reuse_guards
        )

    svc = service()
    r = random.Random(COUNTEREXAMPLE_SEED)
    for step in range(300):
        if step and step % 25 == 0:
            svc = service()  # a restart: the history comes back from the WAL
        ev = _random_event(r)
        got, want = _call(svc, ev), _call(oracle, ev)
        assert (got["allowed"], got["reason"]) == (want["allowed"], want["reason"]), (
            f"step {step}: {ev}"
        )
        if step == COUNTEREXAMPLE_STEP:
            assert ev["tool_name"] == "deploy"
            assert got["allowed"] is True


def _event(agent: str, tool: str, ts: int) -> dict:
    return {"ts": ts, "agent": agent, "type": "tool_call", "tool": tool, "args": {}}


def test_the_wal_keeps_admission_order_across_restarts(tmp_path):
    wal = tmp_path / "wal"
    store = MemoryTraceStore(wal)
    # The ts values tie, and the later event's agent sorts first: the
    # order a (ts, agent) rebuild got wrong.
    store.append(SUB, "c", _event(SUB, "deploy", 2))
    store.append(MAIN, "c", _event(MAIN, "check", 2))
    store.append(MAIN, "c", _event(MAIN, "deploy", 1))
    want = ["deploy", "check", "deploy"]
    assert [e.tool for e in store.events(MAIN, "c")] == want

    for _ in range(2):
        store = MemoryTraceStore(wal)
        assert store.load() == 1
        events = store.events(MAIN, "c")
        assert [e.tool for e in events] == want
        assert [e.ts for e in events] == [0, 1, 2]

    store.append(MAIN, "c", _event(MAIN, "Read", 0))
    assert [e.ts for e in store.events(MAIN, "c")] == [0, 1, 2, 3]
    logged = [json.loads(line)["e"]["seq"] for line in (wal / "c.jsonl").open()]
    assert logged == [0, 1, 2, 3]


def test_a_log_written_without_seq_is_numbered_by_its_order(tmp_path):
    wal = tmp_path / "wal"
    wal.mkdir()
    lines = [
        {"b": SUB, "e": _event(SUB, "deploy", 2)},
        {"b": MAIN, "e": _event(MAIN, "check", 2)},
    ]
    (wal / "c.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    store = MemoryTraceStore(wal)
    store.load()
    assert [(e.tool, e.ts) for e in store.events(MAIN, "c")] == [
        ("deploy", 0),
        ("check", 1),
    ]
    store.append(MAIN, "c", _event(MAIN, "deploy", 0))
    assert [e.ts for e in store.events(MAIN, "c")] == [0, 1, 2]
