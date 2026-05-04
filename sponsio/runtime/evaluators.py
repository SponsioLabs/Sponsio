"""Deterministic evaluation pipeline.

DetEvaluator -> {prop: bool} -> feeds the formula evaluator / Z3.

The OSS engine is det-only. The LLM-judged stochastic pipeline (the
old ``StoEvaluator`` impl that lived here) moved to the proprietary
``sponsio-cloud`` package; the abstract Protocol that describes the
contract Cloud implements lives in :mod:`sponsio.protocols.sto`.
"""

from __future__ import annotations

import logging
from typing import Callable

from sponsio.models.trace import Trace

logger = logging.getLogger(__name__)

# Fail-closed semantics for deterministic evaluators (#17): when a
# registered proposition function raises, the surrounding contract must
# default to *violation*, not silent pass. Returning ``False`` forces
# the evaluator to behave as if the property is NOT satisfied.
_DET_FALLBACK_ON_ERROR: bool = False


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

        Each registered function is invoked in isolation (#17). Previously
        this was a dict-comprehension, so a single buggy evaluator would
        raise out of the whole call and every *other* proposition in the
        contract set became unobservable on that trace — an unbounded
        blast radius for any custom det evaluator glitch. ``StoEvaluator``
        already had ``_safe_evaluate`` with circuit breakers; the det
        path now has the same isolation, minus the breaker complexity
        (det evaluators are pure functions and don't call the network).

        Failure semantics: a raised exception is logged at ``warning``
        level and the proposition is recorded as ``False`` — "proposition
        not observed" is indistinguishable from "proposition refuted",
        and fail-closed is the only safe default for a security guard.

        Args:
            trace: The execution trace to evaluate.

        Returns:
            Dict mapping proposition names to boolean values. Always
            contains a key for every registered proposition — callers
            (the LTL evaluator) rely on this invariant.
        """
        out: dict[str, bool] = {}
        for name, fn in self._evaluators.items():
            try:
                out[name] = bool(fn(trace))
            except Exception as exc:
                logger.warning(
                    "det evaluator %r raised %s: %s — defaulting to %s "
                    "(fail-closed). Other propositions in this evaluation "
                    "are unaffected.",
                    name,
                    type(exc).__name__,
                    exc,
                    _DET_FALLBACK_ON_ERROR,
                )
                out[name] = _DET_FALLBACK_ON_ERROR
        return out

    @property
    def props(self) -> list[str]:
        """Returns the list of registered proposition names."""
        return list(self._evaluators.keys())
