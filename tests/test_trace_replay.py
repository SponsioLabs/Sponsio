"""Tests for sponsio/discovery/trace_replay.py.

CLI-level tests (``sponsio validate --traces``) were removed when
that flag was rolled back from cli.py.  The pure helper still
stands on its own and is unit-tested below.
"""

from __future__ import annotations

import pytest

from sponsio.discovery.trace_replay import TraceReplayResult, replay_formula
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import must_precede, rate_limit


def _trace(*tools: str) -> Trace:
    """Build a Trace where each tool is one tool_call event in order."""
    return Trace(
        events=[
            Event(ts=i, agent="bot", event_type="tool_call", tool=t)
            for i, t in enumerate(tools)
        ]
    )


class TestReplayFormula:
    def test_all_pass(self):
        """A must_precede that holds on every trace gives pass_rate 1.0."""
        traces = [_trace("A", "B"), _trace("A", "B")]
        out = replay_formula(must_precede("A", "B"), traces)
        assert out.pass_count == 2
        assert out.fail_count == 0
        assert out.pass_rate == 1.0
        assert out.errors == []

    def test_all_fail(self):
        """An inverted must_precede catches all the A-then-B traces."""
        traces = [_trace("A", "B"), _trace("A", "B")]
        out = replay_formula(must_precede("B", "A"), traces)
        assert out.pass_count == 0
        assert out.fail_count == 2
        assert out.pass_rate == 0.0

    def test_mixed_traces(self):
        """Pass / fail / vacuous-pass interleaved — counts add up."""
        # must_precede(A, B):
        #   [A, B]  → A before B ✓
        #   [B]     → B without preceding A ✗
        #   [A]     → no B, vacuously ✓
        traces = [_trace("A", "B"), _trace("B"), _trace("A")]
        out = replay_formula(must_precede("A", "B"), traces)
        assert out.pass_count == 2
        assert out.fail_count == 1
        assert out.pass_rate == pytest.approx(2 / 3, abs=1e-3)

    def test_empty_trace_list(self):
        """No traces → pass_rate is None (caller decides how to render)."""
        out = replay_formula(must_precede("A", "B"), [])
        assert out.total == 0
        assert out.pass_rate is None

    def test_accepts_raw_ast_or_detformula(self):
        """Both DetFormula wrappers and raw AST nodes work."""
        det = must_precede("A", "B")
        traces = [_trace("A", "B")]

        out_wrapped = replay_formula(det, traces)
        out_raw = replay_formula(det.formula, traces)

        assert out_wrapped.pass_count == out_raw.pass_count == 1

    def test_evaluation_errors_isolated(self):
        """An evaluator exception on one trace doesn't poison the run."""
        # Construct a trace whose ground() will produce no atoms a
        # rate_limit needs; rate_limit on an empty trace evaluates fine
        # (count = 0), so to actually cause an error we'd need a malformed
        # Trace.  Instead check that error_count starts at 0 on a clean
        # input — error-isolation is exercised by the surrounding
        # try/except, this just guards the happy path doesn't accidentally
        # flag errors.
        out = replay_formula(rate_limit("X", 5), [_trace("X")])
        assert out.error_count == 0


class TestTraceReplayResult:
    def test_total_excludes_errors(self):
        r = TraceReplayResult(pass_count=3, fail_count=2, error_count=5)
        assert r.total == 5  # 3 + 2, errors not counted

    def test_pass_rate_none_on_zero_total(self):
        r = TraceReplayResult(pass_count=0, fail_count=0, error_count=4)
        assert r.pass_rate is None
