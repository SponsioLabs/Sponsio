"""Tests for ``StoEvaluator`` fault-tolerance: fallback_mode + breaker.

The contract these tests lock in:

* A raising evaluator never crashes the agent — the result is
  synthesised according to ``fallback_mode``.
* The circuit breaker trips after N consecutive failures and
  short-circuits subsequent calls during the cooldown window
  (saves latency + avoids piling on a struggling judge).
* A successful call closes the breaker — a flaky judge that
  recovers shouldn't stay tripped forever.
* ``fallback_mode`` is per-instance, but the breaker is per-evaluator
  *within* an instance — one bad judge doesn't silence good ones.
"""

from __future__ import annotations

import pytest

from sponsio.models.trace import Trace
from sponsio.runtime.evaluators import StoEvaluator, StoResult


def _ok(_trace: Trace) -> StoResult:
    return StoResult(score=0.9, evidence="all good", suggestion="")


def _boom(_trace: Trace) -> StoResult:
    raise ConnectionError("LLM timed out")


@pytest.fixture
def trace() -> Trace:
    return Trace(events=[])


# ---------------------------------------------------------------------------
# fallback_mode
# ---------------------------------------------------------------------------


class TestFallbackMode:
    def test_default_is_allow(self, trace: Trace):
        """Production-default: a flaky judge must not block the agent.
        Synthesises a passing score so the agent keeps running.
        """
        ev = StoEvaluator()  # default fallback="allow"
        ev.register("tone", _boom, threshold=0.5)
        results = ev.check(trace)
        assert "tone" in results
        passed, result = results["tone"]
        assert passed is True
        assert result.score == 1.0
        assert result.metadata["sponsio.sto.fallback"] == "allow"

    def test_deny_fails_closed(self, trace: Trace):
        """High-stakes deployments can opt into fail-closed semantics
        — a judge failure becomes a violation, not a free pass."""
        ev = StoEvaluator(fallback_mode="deny", circuit_breaker=False)
        ev.register("injection_free", _boom, threshold=0.5)
        passed, result = ev.check(trace)["injection_free"]
        assert passed is False
        assert result.score == 0.0
        assert result.metadata["sponsio.sto.fallback"] == "deny"

    def test_skip_omits_from_results(self, trace: Trace):
        """``skip`` is the "we don't know — don't count it either way"
        option — useful when a judge is intermittent and you'd rather
        report no signal than a fabricated one."""
        ev = StoEvaluator(fallback_mode="skip", circuit_breaker=False)
        ev.register("tone", _boom, threshold=0.5)
        ev.register("works", _ok, threshold=0.5)

        results = ev.check(trace)
        assert "tone" not in results  # skipped
        assert "works" in results

        # Same shape for ``evaluate``
        evaled = ev.evaluate(trace)
        assert "tone" not in evaled
        assert "works" in evaled

    def test_metadata_marks_fallback(self, trace: Trace):
        """Audit-log readers need to tell a synthetic result from a
        real one — the metadata key ``sponsio.sto.fallback`` is the
        canonical marker."""
        ev = StoEvaluator(fallback_mode="allow", circuit_breaker=False)
        ev.register("tone", _boom)
        _, result = ev.check(trace)["tone"]
        assert result.metadata["sponsio.sto.fallback"] == "allow"
        assert result.metadata["sponsio.sto.evaluator"] == "tone"
        # Real successful call has no fallback marker
        ev2 = StoEvaluator(fallback_mode="allow", circuit_breaker=False)
        ev2.register("tone", _ok)
        _, real = ev2.check(trace)["tone"]
        assert "sponsio.sto.fallback" not in real.metadata


# ---------------------------------------------------------------------------
# circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_trips_after_threshold_failures(self, trace: Trace):
        ev = StoEvaluator(failure_threshold=3, cooldown_seconds=60.0)
        ev.register("tone", _boom)

        for _ in range(3):
            ev.check(trace)

        assert ev.breaker_state("tone").is_tripped(
            now=ev.breaker_state("tone").tripped_until - 1
        )

    def test_open_breaker_short_circuits_without_calling_fn(self, trace: Trace):
        """While the breaker is open, the evaluator must not be
        invoked — the whole point is to stop hammering the judge.
        We assert this by counting calls to a counting fn.
        """
        calls = {"n": 0}

        def counting_boom(_trace: Trace) -> StoResult:
            calls["n"] += 1
            raise RuntimeError("nope")

        ev = StoEvaluator(failure_threshold=2, cooldown_seconds=60.0)
        ev.register("tone", counting_boom)

        ev.check(trace)
        ev.check(trace)
        # Breaker is now tripped — these two calls should NOT invoke the fn
        ev.check(trace)
        ev.check(trace)

        assert calls["n"] == 2, (
            f"expected breaker to suppress calls 3+; got {calls['n']} total"
        )

    def test_success_closes_breaker(self, trace: Trace):
        """A flaky judge that eventually recovers must clear the
        breaker — otherwise transient failures would silence the
        evaluator forever."""
        flaky_calls = {"n": 0}

        def flaky(_trace: Trace) -> StoResult:
            flaky_calls["n"] += 1
            if flaky_calls["n"] <= 2:
                raise TimeoutError("slow")
            return StoResult(score=0.8, evidence="recovered", suggestion="")

        ev = StoEvaluator(failure_threshold=10, cooldown_seconds=60.0)
        ev.register("tone", flaky)

        ev.check(trace)
        ev.check(trace)
        # Breaker not tripped (threshold=10), but consecutive_failures=2
        assert ev.breaker_state("tone").consecutive_failures == 2

        ev.check(trace)  # succeeds
        assert ev.breaker_state("tone").consecutive_failures == 0
        assert ev.breaker_state("tone").tripped_until == 0.0

    def test_per_evaluator_isolation(self, trace: Trace):
        """One bad judge must not trip the breaker for a *different*
        evaluator on the same StoEvaluator instance — each name
        carries its own state."""
        ev = StoEvaluator(failure_threshold=2, cooldown_seconds=60.0)
        ev.register("bad", _boom)
        ev.register("good", _ok)

        for _ in range(5):
            ev.check(trace)

        assert ev.breaker_state("bad").consecutive_failures > 0
        assert ev.breaker_state("good").consecutive_failures == 0
        assert ev.breaker_state("good").tripped_until == 0.0

    def test_disabled_circuit_breaker_still_uses_fallback(self, trace: Trace):
        """``circuit_breaker=False`` should turn off the
        short-circuit / cooldown behavior but still apply
        ``fallback_mode`` on each individual failure — exception
        propagation is what users definitely never want."""
        calls = {"n": 0}

        def counting_boom(_trace: Trace) -> StoResult:
            calls["n"] += 1
            raise RuntimeError("nope")

        ev = StoEvaluator(circuit_breaker=False, fallback_mode="allow")
        ev.register("tone", counting_boom)
        for _ in range(5):
            ev.check(trace)

        assert calls["n"] == 5, "without breaker, every call should hit the fn"

    def test_does_not_affect_successful_evaluators(self, trace: Trace):
        """Sanity: tests above mostly stress failure paths.  Verify
        the happy path is byte-identical to the pre-fault-tolerance
        behavior — a successful evaluator returns its real score
        with no metadata pollution."""
        ev = StoEvaluator()
        ev.register("tone", _ok, threshold=0.5)
        passed, result = ev.check(trace)["tone"]
        assert passed is True
        assert result.score == 0.9
        assert "sponsio.sto.fallback" not in result.metadata
