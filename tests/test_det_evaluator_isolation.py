"""Regression tests for ``DetEvaluator`` per-fn exception isolation (#17).

Context
-------
``DetEvaluator.evaluate`` used to be implemented as a dict
comprehension over ``self._evaluators``. That meant a single raising
proposition function aborted the entire call, so every *other*
proposition in the same contract set also became unobservable on that
trace — a one-line bug in a custom det evaluator quietly silenced the
whole agent's det pipeline.

``StoEvaluator`` already had ``_safe_evaluate`` with fallback and
circuit-breaker semantics for exactly this reason. The det path now
has the same isolation (minus the breaker — det evaluators are pure
functions, no network involved) with fail-closed semantics: a raising
fn is logged and its proposition defaults to ``False``, so downstream
LTL evaluation treats the property as *not satisfied* rather than as
trivially true.
"""

from __future__ import annotations

import logging

from sponsio.models.trace import Trace
from sponsio.runtime.evaluators import DetEvaluator


def _true(_trace: Trace) -> bool:
    return True


def _false(_trace: Trace) -> bool:
    return False


def _boom(_trace: Trace) -> bool:
    raise RuntimeError("custom det evaluator glitched")


class TestDetEvaluatorIsolation:
    def test_raising_fn_does_not_abort_other_evaluators(self, caplog):
        """Pre-fix: a raising ``_boom`` blew up the dict-comp and
        ``_true`` was never observed. Post-fix: every proposition
        appears in the output, and the flaky one is recorded as
        False (fail-closed)."""
        ev = DetEvaluator()
        ev.register("good", _true)
        ev.register("bad", _boom)
        ev.register("other", _false)

        with caplog.at_level(logging.WARNING, logger="sponsio.runtime.evaluators"):
            result = ev.evaluate(Trace(events=[]))

        assert set(result.keys()) == {"good", "bad", "other"}, (
            "every registered prop must appear in the output; the LTL "
            "evaluator depends on this invariant"
        )
        assert result["good"] is True
        assert result["other"] is False
        assert result["bad"] is False, "fail-closed on exception"
        # Surface the fault so operators notice — but keep going.
        assert any("bad" in rec.message for rec in caplog.records)

    def test_no_exceptions_happy_path(self):
        """Regression: isolating exceptions must not change the happy-
        path result."""
        ev = DetEvaluator()
        ev.register("a", _true)
        ev.register("b", _false)
        assert ev.evaluate(Trace(events=[])) == {"a": True, "b": False}

    def test_all_evaluators_raise(self):
        """If every evaluator is broken, we still return a full dict
        with all props defaulted — the LTL evaluator should never see
        a missing key."""
        ev = DetEvaluator()
        ev.register("p", _boom)
        ev.register("q", _boom)
        result = ev.evaluate(Trace(events=[]))
        assert result == {"p": False, "q": False}

    def test_non_bool_return_coerced(self):
        """Bool-coercion keeps the ``dict[str, bool]`` contract intact
        even if a sloppy evaluator returns a truthy object."""
        ev = DetEvaluator()
        ev.register("truthy", lambda _t: "yes")  # type: ignore[arg-type,return-value]
        ev.register("falsy", lambda _t: 0)  # type: ignore[arg-type,return-value]
        result = ev.evaluate(Trace(events=[]))
        assert result == {"truthy": True, "falsy": False}
        assert all(isinstance(v, bool) for v in result.values())
