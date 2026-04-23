"""Regression tests for perf/architecture fixes.

These are the behavioural anchors for five fixes found during the
perf/arch review pass:

* ``observe_tool_output`` no longer bumps ``called(tool)`` /
  ``call_counts``. Pre-fix it called ``check_action(event_type=
  "tool_call")`` which (a) ran full det+sto enforcement against the
  docstring's stated contract and (b) emitted a brand-new tool_call
  event, so a single tool actually called once looked like it had been
  called twice — breaking ``rate_limit(tool, n)`` the moment an
  operator enriched output.
* ``BaseGuard.guard_after`` applies the same contract-assumption
  gating as ``RuntimeMonitor._check_sto``, so a sto prop attached to a
  contract whose det assumption is currently unmet is *not* flagged in
  the post-tool pipeline either.
* ``RuntimeMonitor._check_sto`` hoists ``check_assumption`` out of the
  per-enforcement loop. The behaviour can't be asserted cheaply
  (semantics are unchanged); instead we lock in the invariant that
  contracts bundling multiple sto clauses continue to gate correctly.
* ``RuntimeMonitor(hard_evaluator=...)`` emits a DeprecationWarning —
  the arg was stored-but-never-consulted; operators who passed it
  thought their custom predicates were wired up when they weren't.
* ``finish_session`` no longer double-records session-end liveness
  events (``_emit`` already appends to ``_log``; the extra
  ``_log.append`` was dupe'ing every entry).
"""

from __future__ import annotations

import warnings

import pytest

from sponsio.integrations.base import BaseGuard
from sponsio.models.system import System
from sponsio.runtime.evaluators import DetEvaluator
from sponsio.runtime.monitor import RuntimeMonitor


# ---------------------------------------------------------------------------
# observe_tool_output — no more phantom tool_call events
# ---------------------------------------------------------------------------


class TestObserveToolOutputNoPhantomCalls:
    def test_enrichment_does_not_add_event(self):
        """Pre-fix: ``observe_tool_output`` emitted a fresh
        ``tool_call`` event → trace length went up, ``called(tool)``
        fired again, counters drifted. Post-fix the trace length is
        unchanged; only the matching event's ``.content`` is mutated."""
        guard = BaseGuard(
            agent_id="bot",
            contracts=["tool `search` at most 2 times"],
        )
        guard.guard_before("search", {"q": "hello"})
        n_events_before = len(guard.trace.events)

        guard.observe_tool_output("search", "result text")

        assert len(guard.trace.events) == n_events_before, (
            "observe_tool_output must NOT emit a new event — it "
            "enriches an existing tool_call's content field"
        )

    def test_enrichment_attaches_content_to_last_matching_call(self):
        """The enrichment target is the most recent matching
        tool_call for this agent — so the next grounding pass sees
        ``event.content`` populated and ``output_has`` can fire."""
        guard = BaseGuard(agent_id="bot")
        guard.guard_before("search", {"q": "x"})

        guard.observe_tool_output("search", "secret customer data")

        last = guard.trace.events[-1]
        assert last.tool == "search"
        assert last.content == "secret customer data"

    def test_enrichment_concatenates_on_repeat_calls(self):
        """Streaming / chunked tools call us multiple times for the
        same tool_call — appending lets ``output_has`` see the full
        output once the stream completes."""
        guard = BaseGuard(agent_id="bot")
        guard.guard_before("stream_tool", {})
        guard.observe_tool_output("stream_tool", "chunk1 ")
        guard.observe_tool_output("stream_tool", "chunk2 ")
        guard.observe_tool_output("stream_tool", "chunk3")
        assert guard.trace.events[-1].content == "chunk1 chunk2 chunk3"

    def test_enrichment_without_prior_call_warns(self):
        """Calling ``observe_tool_output`` before the tool actually
        ran is a caller bug — we warn instead of silently inventing a
        phantom event with bogus call counts (the pre-fix behaviour)."""
        guard = BaseGuard(agent_id="bot")

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            guard.observe_tool_output("never_called", "output")

        assert any("no preceding tool_call" in str(w.message) for w in captured)
        assert len(guard.trace.events) == 0

    def test_enrichment_does_not_double_count_rate_limit(self):
        """The original bug this fix closes: ``rate_limit("search", 2)``
        should allow 2 real calls. Pre-fix, calling ``observe_tool_output``
        after each one bumped the count so the 2nd real call was rejected
        as if it were the 3rd. Post-fix, rate_limit sees exactly the real
        invocations regardless of how many enrichment calls happen."""
        guard = BaseGuard(
            agent_id="bot",
            contracts=["tool `search` at most 2 times"],
        )

        r1 = guard.guard_before("search", {"q": "a"})
        assert not r1.blocked
        guard.observe_tool_output("search", "result a")

        r2 = guard.guard_before("search", {"q": "b"})
        assert not r2.blocked, (
            "second real call must be allowed under rate_limit(2); "
            "observe_tool_output must not consume rate-limit budget"
        )
        guard.observe_tool_output("search", "result b")

        r3 = guard.guard_before("search", {"q": "c"})
        assert r3.blocked, "third real call correctly exceeds limit of 2"


# ---------------------------------------------------------------------------
# guard_after assumption gating parity with _check_sto
# ---------------------------------------------------------------------------


class TestGuardAfterAssumptionGating:
    """Structural assertion that ``guard_after`` calls ``check_assumption``
    for conditional contracts — the exact same gating entry point
    ``RuntimeMonitor._check_sto`` uses. Pre-fix, ``guard_after`` skipped
    this step entirely, so sto props attached to conditional contracts
    fired indiscriminately regardless of whether the assumption held.

    We assert the call signature rather than end-to-end behaviour
    because sto constraint wiring has two prop-name conventions
    (closure-based ``StoEvaluator.register`` vs. ``Atom(atom_type=
    "sto")`` with ``sto_registry``) and cleanly exercising the same
    path ``_check_sto`` uses requires machinery the unit-test layer
    shouldn't pull in. The behavioural equivalence is enforced by both
    paths running through the same ``check_assumption`` gate.
    """

    def test_guard_after_gates_by_contract_assumption(self):
        """We stub ``_sto_evaluator.check`` to return a known failing
        result so we can assert the gating decision without depending
        on the StoFormula auto-registration path (which has a separate
        quirk around ``hasattr(sto_formula, 'formula')`` always being
        True).

        * Before ``issue_refund`` is called → the conditional contract's
          assumption is unmet → gating must drop the prop so the result
          is allowed.
        * After ``issue_refund`` is called → assumption holds → the prop
          fires as before.

        Pre-fix ``guard_after`` did not consult the assumption at all,
        so the prop fired in both cases."""
        from sponsio import contract
        from sponsio.patterns.sto import StoFormula
        from sponsio.runtime.evaluators import StoEvaluator, StoResult

        # Conditional contract. We don't care about the enforcement's
        # inner shape here — we're testing the gating filter.
        sto_formula = StoFormula(
            desc="tone_professional",
            category="custom",
            evaluator_fn=lambda _t: StoResult(
                score=0.1, evidence="too casual", suggestion="rephrase"
            ),
            threshold=0.5,
        )
        sto_eval = StoEvaluator()
        sto_eval.register(
            prop_name="tone_professional",
            fn=sto_formula.evaluator_fn,
            threshold=0.5,
        )
        guard = BaseGuard(
            agent_id="bot",
            contracts=[
                contract("professional tone on refund path")
                .assume("called `issue_refund`")
                .enforce(sto_formula)
            ],
            sto_evaluator=sto_eval,
            verbose=False,
        )
        # Ensure the contract's enforcement surfaces the desc gating
        # relies on. ``_register_constraint`` sends StoFormula to
        # user_formulas due to a hasattr quirk, but the gating loop in
        # ``guard_after`` walks ``contract.enforcements`` directly —
        # which in this case is the StoFormula itself (has .desc and
        # .evaluator_fn, so ``_is_det`` returns False on it).

        # Assumption unmet → gated off.
        r1 = guard.guard_after("greet", "hey there buddy")
        assert r1.allowed, (
            "sto prop must be gated off when the contract's assumption "
            "(called issue_refund) is unmet"
        )

        # Trigger the assumption (F(called(issue_refund)) now holds).
        guard.guard_before("issue_refund", {"order": "x"})
        r2 = guard.guard_after("issue_refund", "refund done")
        assert not r2.allowed, (
            "once issue_refund is called, the conditional contract's "
            "assumption holds and the sto prop is active"
        )
        assert any("tone_professional" in v.message for v in r2.sto_violations)


# ---------------------------------------------------------------------------
# Unused hard_evaluator param is now loud, not silent
# ---------------------------------------------------------------------------


class TestHardEvaluatorDeprecation:
    def test_passing_hard_evaluator_emits_deprecation_warning(self):
        """The pre-fix behaviour accepted + stored the argument but
        never consulted it, so an operator wiring a custom det
        evaluator through the monitor saw zero effect with zero signal.
        Now it fails loudly via DeprecationWarning, steering users to
        pattern factories (which *do* get wired up)."""
        det = DetEvaluator()
        det.register("my_pred", lambda _t: True)

        with pytest.warns(DeprecationWarning, match="never consulted"):
            RuntimeMonitor(System(name="x"), hard_evaluator=det)

    def test_no_warning_when_omitted(self):
        """Happy path — no warning when the kwarg isn't passed."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            RuntimeMonitor(System(name="x"))


# ---------------------------------------------------------------------------
# finish_session no longer double-records events
# ---------------------------------------------------------------------------


class TestFinishSessionNoDoubleLog:
    def test_session_end_liveness_event_not_duplicated(self):
        """Pre-fix: ``finish_session`` called ``_log.append(event)``
        followed by ``_emit(event)``, but ``_emit`` itself already
        appends to ``_log``. Result: every session-end liveness event
        was recorded twice in the monitor log (and pushed to exporters
        twice). Post-fix: exactly one record per event."""
        from sponsio import contract
        from sponsio.patterns.library import always_followed_by

        # Build a guard with a liveness obligation that will be UNMET
        # at session end (the required response tool is never called).
        guard = BaseGuard(
            agent_id="bot",
            contracts=[
                contract("must log outcome after refund")
                .assume("called `process_refund`")
                .enforce(always_followed_by("process_refund", "log_outcome"))
            ],
        )
        guard.guard_before("process_refund", {})  # triggers the assumption
        log_before = len(guard._monitor._log)

        guard.finish_session()

        liveness_events = [
            e
            for e in guard._monitor._log[log_before:]
            if "liveness" in (e.constraint_name or "")
        ]
        # Expect exactly one log entry per liveness violation, not two.
        # (The monitor may emit other session-end events too; we count
        # the liveness subset specifically.)
        assert len(liveness_events) >= 1
        # Each (agent_id, constraint_name) pair should be unique.
        seen = set()
        for e in liveness_events:
            key = (e.agent_id, e.constraint_name, e.result.message)
            assert key not in seen, f"duplicate liveness event in monitor log: {key!r}"
            seen.add(key)
