"""Dual evaluation pipeline: det (boolean) and sto (scored) constraint checking.

Hard path: DetEvaluator -> {prop: bool} -> feeds existing formula evaluator / Z3.
Soft path: StoEvaluator -> StoResult(score, evidence, suggestion) -> threshold check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sponsio.models.trace import Trace


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


class StoEvaluator:
    """Sto constraint evaluator: maps proposition names to scored functions.

    Each registered function takes a Trace and returns a StoResult with
    a confidence score, evidence, and suggestion. The threshold determines
    whether the constraint passes or fails.
    """

    def __init__(self) -> None:
        self._evaluators: dict[str, _SoftEntry] = {}

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

    def evaluate(self, trace: Trace) -> dict[str, StoResult]:
        """Evaluates all registered sto constraints against a trace.

        Args:
            trace: The execution trace to evaluate.

        Returns:
            Dict mapping constraint names to StoResult objects.
        """
        return {name: entry.fn(trace) for name, entry in self._evaluators.items()}

    def check(self, trace: Trace) -> dict[str, tuple[bool, StoResult]]:
        """Evaluates and checks all sto constraints against their thresholds.

        Args:
            trace: The execution trace to evaluate.

        Returns:
            Dict mapping constraint names to (passed, StoResult) tuples.
        """
        results: dict[str, tuple[bool, StoResult]] = {}
        for name, entry in self._evaluators.items():
            result = entry.fn(trace)
            passed = result.score >= entry.threshold
            results[name] = (passed, result)
        return results

    def get_threshold(self, prop_name: str) -> float:
        """Returns the threshold for a registered constraint."""
        return self._evaluators[prop_name].threshold

    def get_feedback_template(self, prop_name: str) -> str | None:
        """Returns the feedback template for a registered constraint, if any."""
        return self._evaluators[prop_name].feedback_template

    @property
    def props(self) -> list[str]:
        """Returns the list of registered constraint names."""
        return list(self._evaluators.keys())
