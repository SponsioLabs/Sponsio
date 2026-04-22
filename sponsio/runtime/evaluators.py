"""Dual evaluation pipeline: det (boolean) and sto (scored) constraint checking.

Hard path: DetEvaluator -> {prop: bool} -> feeds existing formula evaluator / Z3.
Soft path: StoEvaluator -> StoResult(score, evidence, suggestion) -> threshold check.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from sponsio.models.trace import Trace

logger = logging.getLogger(__name__)


FallbackMode = Literal["allow", "deny", "skip"]


@dataclass
class StoResult:
    """Result of a sto constraint evaluation.

    Attributes:
        score: Confidence score in [0, 1]. Higher means more compliant.
        evidence: What triggered the score (e.g. "contains aggressive language").
        suggestion: Actionable fix hint (e.g. "rephrase using neutral tone").
        metadata: Optional extras (entropy, model info, etc.).
    """

    score: float
    evidence: str
    suggestion: str
    metadata: dict = field(default_factory=dict)


class DetEvaluator:
    """Det constraint evaluator: maps proposition names to boolean functions.

    Each registered function takes a Trace and returns True/False.
    The resulting dict feeds directly into the existing formula evaluator.
    """

    def __init__(self) -> None:
        self._evaluators: dict[str, Callable[[Trace], bool]] = {}

    def register(self, prop_name: str, fn: Callable[[Trace], bool]) -> None:
        """Registers a boolean evaluator for a proposition.

        Args:
            prop_name: Predicate/proposition name (e.g. "called(fraud_check)").
            fn: Function that takes a Trace and returns bool.
        """
        self._evaluators[prop_name] = fn

    def evaluate(self, trace: Trace) -> dict[str, bool]:
        """Evaluates all registered propositions against a trace.

        Args:
            trace: The execution trace to evaluate.

        Returns:
            Dict mapping proposition names to boolean values.
        """
        return {name: fn(trace) for name, fn in self._evaluators.items()}

    @property
    def props(self) -> list[str]:
        """Returns the list of registered proposition names."""
        return list(self._evaluators.keys())


@dataclass
class _SoftEntry:
    """Internal: a registered sto evaluator with its config."""

    fn: Callable[[Trace], StoResult]
    threshold: float
    feedback_template: str | None


@dataclass
class _BreakerState:
    """Per-evaluator circuit-breaker state.

    Borrowed from the standard half-open / closed / open state machine
    (see Nygard, *Release It!*).  The interesting bit for Sponsio is
    that breaker state is **per evaluator name** — one flaky judge
    (``injection_free``) shouldn't trip the whole sto pipeline and
    silence working ones (``tone_professional``).
    """

    consecutive_failures: int = 0
    tripped_until: float = 0.0  # unix timestamp; 0.0 = closed

    def is_tripped(self, now: float) -> bool:
        return self.tripped_until > now

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.tripped_until = 0.0

    def record_failure(self, threshold: int, cooldown: float, now: float) -> bool:
        """Returns True iff this failure tripped the breaker."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= threshold:
            self.tripped_until = now + cooldown
            return True
        return False


class StoEvaluator:
    """Sto constraint evaluator: maps proposition names to scored functions.

    Each registered function takes a Trace and returns a StoResult with
    a confidence score, evidence, and suggestion. The threshold determines
    whether the constraint passes or fails.

    LLM-judge resilience:

    * **fallback_mode** controls what we return when an evaluator
      raises (typically a network/LLM error).  Production-default is
      ``allow`` — agent-blocking failures shouldn't cascade from a
      flaky judge.  Set ``deny`` for high-stakes deployments where
      "fail closed" is preferable, or ``skip`` to omit the result
      from ``check()`` entirely (no contribution to violations).

    * **circuit_breaker** short-circuits subsequent calls to a
      consistently-failing evaluator.  After ``failure_threshold``
      consecutive failures the breaker trips, and for the next
      ``cooldown_seconds`` the evaluator returns the fallback result
      immediately without attempting a real call (saving latency and
      not piling up on a struggling judge).  After cooldown the
      breaker enters half-open and tries one real call.
    """

    def __init__(
        self,
        fallback_mode: FallbackMode = "allow",
        circuit_breaker: bool = True,
        failure_threshold: int = 5,
        cooldown_seconds: float = 10.0,
    ) -> None:
        self._evaluators: dict[str, _SoftEntry] = {}
        self._fallback_mode: FallbackMode = fallback_mode
        self._circuit_breaker = circuit_breaker
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._breakers: dict[str, _BreakerState] = {}

    def register(
        self,
        prop_name: str,
        fn: Callable[[Trace], StoResult],
        threshold: float = 0.5,
        feedback_template: str | None = None,
    ) -> None:
        """Registers a scored evaluator for a proposition.

        Args:
            prop_name: Constraint name (e.g. "tone_appropriate").
            fn: Function that takes a Trace and returns StoResult.
            threshold: Minimum score to pass (default 0.5).
            feedback_template: Optional template for discriminative feedback.
                Supports placeholders: {name}, {score}, {evidence}, {suggestion}.
        """
        self._evaluators[prop_name] = _SoftEntry(
            fn=fn,
            threshold=threshold,
            feedback_template=feedback_template,
        )

    def _fallback_result(self, name: str, reason: str) -> StoResult | None:
        """Synthesise a result when the real evaluator can't run.

        Returns ``None`` for ``skip`` so callers can detect-and-omit;
        otherwise a synthetic ``StoResult`` whose score guarantees the
        chosen pass/fail outcome regardless of the registered
        threshold.  ``metadata`` carries the marker so observers
        (audit log, OTel span attrs) can tell a fallback from a real
        score.
        """
        if self._fallback_mode == "skip":
            return None
        score = 1.0 if self._fallback_mode == "allow" else 0.0
        return StoResult(
            score=score,
            evidence=f"sto judge unavailable ({reason})",
            suggestion="check llm-judge connectivity / credentials",
            metadata={
                "sponsio.sto.fallback": self._fallback_mode,
                "sponsio.sto.evaluator": name,
            },
        )

    def _safe_evaluate(
        self, name: str, entry: _SoftEntry, trace: Trace
    ) -> StoResult | None:
        """Wrap ``entry.fn(trace)`` with breaker + fallback semantics.

        Returns ``None`` only when fallback_mode is ``skip`` AND a
        failure occurred — caller treats this as "not evaluated, no
        violation contribution".
        """
        now = time.monotonic()
        breaker = self._breakers.setdefault(name, _BreakerState())

        if self._circuit_breaker and breaker.is_tripped(now):
            return self._fallback_result(name, "circuit-breaker open")

        try:
            # Legacy sto evaluators are closure-based and we can't
            # peek inside to see if they call an LLM — treat the
            # whole invocation as "live" for perf bucketing purposes.
            # Under-counts "actually used a cache" at worst, never
            # over-claims zero-LLM activity (which would be the
            # dangerous direction).
            from sponsio.runtime.perf import _increment_counter as _perf_llm_bump

            _perf_llm_bump()
            result = entry.fn(trace)
        except Exception as exc:
            if self._circuit_breaker:
                tripped = breaker.record_failure(
                    self._failure_threshold, self._cooldown_seconds, now
                )
                if tripped:
                    logger.warning(
                        "sto evaluator %r tripped circuit breaker after "
                        "%d consecutive failures; falling back for %.1fs",
                        name,
                        breaker.consecutive_failures,
                        self._cooldown_seconds,
                    )
            else:
                logger.warning("sto evaluator %r failed: %s", name, exc)
            return self._fallback_result(name, type(exc).__name__)

        # Success — close the breaker so a flaky judge that
        # eventually recovers doesn't stay tripped forever.
        if self._circuit_breaker:
            breaker.record_success()
        return result

    def evaluate(self, trace: Trace) -> dict[str, StoResult]:
        """Evaluates all registered sto constraints against a trace.

        Args:
            trace: The execution trace to evaluate.

        Returns:
            Dict mapping constraint names to StoResult objects.
            ``skip``-mode evaluators that failed are omitted from the
            output entirely.
        """
        out: dict[str, StoResult] = {}
        for name, entry in self._evaluators.items():
            result = self._safe_evaluate(name, entry, trace)
            if result is not None:
                out[name] = result
        return out

    def check(self, trace: Trace) -> dict[str, tuple[bool, StoResult]]:
        """Evaluates and checks all sto constraints against their thresholds.

        Args:
            trace: The execution trace to evaluate.

        Returns:
            Dict mapping constraint names to (passed, StoResult)
            tuples.  Skipped (``fallback_mode='skip'``) evaluators
            don't appear in the output.
        """
        results: dict[str, tuple[bool, StoResult]] = {}
        for name, entry in self._evaluators.items():
            result = self._safe_evaluate(name, entry, trace)
            if result is None:
                continue
            passed = result.score >= entry.threshold
            results[name] = (passed, result)
        return results

    def get_threshold(self, prop_name: str) -> float:
        """Returns the threshold for a registered constraint."""
        return self._evaluators[prop_name].threshold

    def get_feedback_template(self, prop_name: str) -> str | None:
        """Returns the feedback template for a registered constraint, if any."""
        return self._evaluators[prop_name].feedback_template

    def breaker_state(self, prop_name: str) -> _BreakerState:
        """Inspect the circuit-breaker state for an evaluator.

        Useful for ``sponsio doctor`` and per-evaluator dashboards;
        returns a default closed breaker if the evaluator has never
        been called.
        """
        return self._breakers.get(prop_name, _BreakerState())

    @property
    def props(self) -> list[str]:
        """Returns the list of registered constraint names."""
        return list(self._evaluators.keys())
