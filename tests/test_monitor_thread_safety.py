"""Regression tests for ``RuntimeMonitor`` thread safety.

Two code paths drive ``RuntimeMonitor.check_action`` from multiple
threads in production:

1. ``api/state.py`` — a single ``AppState.monitor`` is shared across
   FastAPI sync routes, which FastAPI dispatches on a thread pool.
2. ``sponsio.integrations.mcp.MCPContractProxy`` — the proxy is a
   singleton serving concurrent tool clients.

Pre-fix the ``check_action`` body was unlocked: ``trace.events.append``
+ ``verifier.sync`` + ``_atom_caches`` writes + ``_turn_spans.append``
all happened without any mutex. Two threads could interleave such that
(a) two events got the same ``ts`` (both read ``len`` before either
appended), (b) ``verifier._grounded_upto`` lagged behind
``len(trace.events)`` producing missing valuations, or (c)
``_atom_caches`` read the wrong cache entry for the wrong contract.

These tests pound the monitor with concurrent ``check_action`` and
assert that:

- All events get unique, contiguous ``ts`` values (``0..N-1``).
- ``len(trace.events) == N`` after ``N`` concurrent calls.
- ``len(turn_spans) == N`` — no dropped spans.
- No ``IndexError`` / ``KeyError`` / ``RuntimeError`` escaped the
  pipeline during the run.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sponsio.models.system import System
from sponsio.runtime.monitor import RuntimeMonitor


def _build_monitor() -> RuntimeMonitor:
    # Bare system — contracts aren't needed to exercise the race,
    # since the data race is in the event/span/verifier plumbing of
    # ``check_action`` itself, not in any individual contract check.
    return RuntimeMonitor(System("t"))


class TestCheckActionRace:
    def test_concurrent_check_action_preserves_event_order(self) -> None:
        mon = _build_monitor()
        n_threads = 16
        n_calls_each = 20
        total = n_threads * n_calls_each
        errors: list[BaseException] = []
        barrier = threading.Barrier(n_threads)

        def worker(thread_idx: int) -> None:
            try:
                barrier.wait()  # maximise contention at start
                for i in range(n_calls_each):
                    mon.check_action(
                        agent_id="a",
                        action="t",
                        metadata={"args": {"thread": thread_idx, "i": i}},
                    )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futs = [pool.submit(worker, t) for t in range(n_threads)]
            for f in futs:
                f.result()

        assert not errors, f"exceptions in concurrent check_action: {errors!r}"
        events = mon.trace.events
        assert len(events) == total, (
            f"expected {total} events after concurrent calls, "
            f"got {len(events)} — indicates lost append (race on list)"
        )
        # ts values must be the exact set 0..total-1 (no dup, no gap).
        ts_values = sorted(ev.ts for ev in events)
        assert ts_values == list(range(total)), (
            "ts values not contiguous — two threads read len() before either appended"
        )

        # turn_spans should have exactly one entry per check_action.
        spans = mon.turn_spans
        assert len(spans) == total, (
            f"expected {total} turn spans, got {len(spans)} — race on _turn_spans.append"
        )

    def test_reset_concurrent_with_check_action_is_safe(self) -> None:
        """``reset`` and ``check_action`` must not interleave partially.

        If ``reset`` clears ``trace.events`` while ``check_action`` has
        already computed ``ts = len(trace.events)`` but not yet
        appended, the appended event takes a stale position. Under
        the lock, ``reset`` either wins (everything wiped) or loses
        (the event was fully recorded first) — never a mix.
        """
        mon = _build_monitor()
        errors: list[BaseException] = []
        stop = threading.Event()

        def writer() -> None:
            try:
                while not stop.is_set():
                    mon.check_action(agent_id="a", action="t")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def resetter() -> None:
            try:
                for _ in range(50):
                    mon.reset()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        w_threads = [threading.Thread(target=writer) for _ in range(4)]
        r_thread = threading.Thread(target=resetter)

        for t in w_threads:
            t.start()
        r_thread.start()

        r_thread.join()
        stop.set()
        for t in w_threads:
            t.join()

        assert not errors, f"exceptions during concurrent reset+check: {errors!r}"
        # Final consistency check: whatever events survived the last
        # reset must have contiguous ts starting at 0.
        events = mon.trace.events
        if events:
            ts_values = sorted(ev.ts for ev in events)
            assert ts_values == list(range(len(events))), (
                "events present after racing reset have gaps/dupes in ts"
            )

    def test_log_snapshot_does_not_tear(self) -> None:
        """``monitor.log`` must return a stable list snapshot even
        while ``_emit`` is appending on another thread. Pre-fix this
        already took ``_lock``; this test pins it down as a contract."""
        mon = _build_monitor()
        errors: list[BaseException] = []
        stop = threading.Event()

        def writer() -> None:
            try:
                while not stop.is_set():
                    mon.check_action(agent_id="a", action="t")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(200):
                    snap = mon.log
                    # Just observing the list — indexing during
                    # iteration would raise IndexError if the
                    # snapshot wasn't really a snapshot.
                    _ = list(snap)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        r.join()
        stop.set()
        w.join()

        assert not errors, f"log snapshot tore under concurrent write: {errors!r}"
