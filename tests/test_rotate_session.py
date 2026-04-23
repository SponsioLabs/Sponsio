"""Regression tests for ``RuntimeMonitor.rotate_session`` /
``BaseGuard.rotate_session``.

This is the memory-management primitive for long-running agents.
Without rotation, ``trace.events``, ``_turn_spans``, ``_log``,
``_atom_caches``, and ``BaseGuard._violations`` grow monotonically
across the lifetime of the monitor — which is correct for
whole-trace LTL semantics but unusable for a 24/7 service agent.

Tests pin down:

* Counts in the returned summary match the window that just closed
  (pre-reset) — these numbers are what ops ship to dashboards.
* Rotation clears trace/log/span/atom-cache state and lets fresh
  events start at ts=0.
* Contracts on the underlying ``System`` survive rotation — this is
  the whole point vs a fresh ``BaseGuard`` instance.
* ``run_finish_session=True`` (default) flushes pending liveness
  obligations before wiping the trace, so an agent whose ``response``
  was promised-but-never-sent still gets a violation recorded.
* ``require_finish_session=True`` raises instead of silently dropping
  obligations when the caller forgot to finalise.
* Rotation is idempotent: calling it on an empty monitor returns
  zero-everywhere and doesn't blow up.
"""

from __future__ import annotations

import warnings

import pytest

from sponsio.integrations.base import BaseGuard
from sponsio.models.system import System
from sponsio.patterns.library import always_followed_by, rate_limit
from sponsio.runtime.monitor import RuntimeMonitor


def _make_guard_with_rate_limit() -> BaseGuard:
    """Guard that caps ``search`` to 2 calls — simple det contract
    that blocks on the 3rd call, giving us a reproducible violation
    path for ``_violations`` accounting."""
    sys = System("t")
    sys.agent("a").tools("search", "respond").enforces(rate_limit("search", 2))
    return BaseGuard(agent_id="a", system=sys)


class TestMonitorRotateSession:
    def test_returns_window_counts_before_reset(self) -> None:
        mon = RuntimeMonitor(System("t"))
        for i in range(5):
            mon.check_action(agent_id="a", action="t")

        summary = mon.rotate_session()
        assert summary["events"] == 5
        assert summary["turns"] == 5
        # log_entries can be 0 if no contract was evaluated; we don't
        # pin the exact number because it depends on registered
        # callbacks and emit policy — but it must be a non-negative
        # int (i.e. the key exists and is sane).
        assert isinstance(summary["log_entries"], int)
        assert summary["log_entries"] >= 0
        assert summary["violations_cleared"] == 0  # monitor-layer, always 0

    def test_clears_all_state(self) -> None:
        mon = RuntimeMonitor(System("t"))
        for _ in range(3):
            mon.check_action(agent_id="a", action="t")

        assert len(mon.trace.events) == 3
        mon.rotate_session()
        assert len(mon.trace.events) == 0
        assert len(mon.turn_spans) == 0
        assert len(mon.log) == 0
        # Next event starts fresh at ts=0 — this is the whole point.
        mon.check_action(agent_id="a", action="t")
        assert mon.trace.events[0].ts == 0

    def test_contracts_survive_rotation(self) -> None:
        """Rotation clears the *trace*, not the *system*. A guard that
        previously enforced a contract must still enforce it after
        rotation — that's the difference from building a fresh guard."""
        g = _make_guard_with_rate_limit()
        # Burn through the limit.
        r1 = g.guard_before("search")
        r2 = g.guard_before("search")
        r3 = g.guard_before("search")
        assert not r1.blocked
        assert not r2.blocked
        assert r3.blocked, "3rd call should be blocked by rate_limit(2)"

        g.rotate_session()

        # New window — limit applies fresh.
        s1 = g.guard_before("search")
        s2 = g.guard_before("search")
        s3 = g.guard_before("search")
        assert not s1.blocked
        assert not s2.blocked
        assert s3.blocked, "rotation must not have wiped the contract"


class TestGuardRotateSession:
    def test_summary_includes_guard_level_violations(self) -> None:
        g = _make_guard_with_rate_limit()
        for _ in range(3):
            g.guard_before("search")

        # 1 violation expected (3rd call). We don't assert exactly 1
        # because emit policy around blocked rollback can vary; we
        # just assert the count is surfaced at all (vs dropped).
        pre_violations = len(g._violations)
        assert pre_violations >= 1

        summary = g.rotate_session()
        assert summary["violations_cleared"] == pre_violations
        assert summary["events"] >= 1
        assert len(g._violations) == 0
        assert len(g._monitor.trace.events) == 0

    def test_runs_finish_session_by_default(self) -> None:
        """Pending liveness obligation should be recorded as a
        violation *before* rotation wipes the trace."""
        sys = System("t")
        # "Every trigger must eventually be followed by respond" —
        # classic liveness formula that only fires at session end.
        sys.agent("a").tools("trigger", "respond").enforces(
            always_followed_by("trigger", "respond")
        )
        g = BaseGuard(agent_id="a", system=sys)

        g.guard_before("trigger")  # promise unfulfilled
        # No respond call.

        summary = g.rotate_session()
        # finish_session should have ran and recorded at least one
        # pending-liveness violation for the unfulfilled trigger.
        assert summary["pending_liveness_violations"] >= 1, (
            "rotate_session(run_finish_session=True) must flush pending "
            "liveness obligations before wiping the trace"
        )

    def test_skips_finish_session_when_opted_out(self) -> None:
        sys = System("t")
        sys.agent("a").tools("trigger", "respond").enforces(
            always_followed_by("trigger", "respond")
        )
        g = BaseGuard(agent_id="a", system=sys)

        g.guard_before("trigger")

        summary = g.rotate_session(run_finish_session=False)
        # Opted out — we expect the pending-liveness counter to
        # reflect whatever was already in the bucket (zero, since
        # finish_session never ran).
        assert summary["pending_liveness_violations"] == 0
        assert not g._finish_session_called

    def test_require_finish_session_raises_when_not_called(self) -> None:
        g = _make_guard_with_rate_limit()
        g.guard_before("search")

        with pytest.raises(RuntimeError, match="finish_session"):
            g.rotate_session(
                run_finish_session=False,
                require_finish_session=True,
            )

    def test_require_finish_session_passes_when_called(self) -> None:
        g = _make_guard_with_rate_limit()
        g.guard_before("search")
        g.finish_session()

        summary = g.rotate_session(
            run_finish_session=False,
            require_finish_session=True,
        )
        assert summary["events"] >= 1

    def test_idempotent_on_empty_guard(self) -> None:
        g = _make_guard_with_rate_limit()
        summary = g.rotate_session()
        assert summary["events"] == 0
        assert summary["turns"] == 0
        assert summary["violations_cleared"] == 0
        assert summary["pending_liveness_violations"] == 0

        # And again — still safe.
        summary2 = g.rotate_session()
        assert summary2["events"] == 0

    def test_finish_session_failure_does_not_block_rotation(self) -> None:
        """If ``finish_session`` raises, rotation must still complete —
        otherwise a crash in the liveness check would leak memory
        indefinitely."""
        g = _make_guard_with_rate_limit()
        g.guard_before("search")

        orig_finish_session = g.finish_session

        def boom() -> list:
            raise RuntimeError("synthetic finish_session crash")

        g.finish_session = boom  # type: ignore[method-assign]
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                summary = g.rotate_session()
            assert any(
                "finish_session raised" in str(w.message) for w in caught
            ), "rotate_session must warn when finish_session raises"
            assert summary["events"] >= 1
            assert len(g._monitor.trace.events) == 0
        finally:
            g.finish_session = orig_finish_session  # type: ignore[method-assign]
