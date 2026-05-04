"""Tests for BaseGuard.finish_session() — end-of-session liveness checks.

Liveness formulas (``always_followed_by`` = ``G(trigger -> F(response))``)
can't be decided mid-session. They're skipped by the runtime monitor and
only judged at session end via :meth:`BaseGuard.finish_session`.
"""

from __future__ import annotations


import sponsio
from sponsio.runtime.verifier import Verdict


def _make_guard(contracts):
    return sponsio.Sponsio(
        agent_id="bot",
        contracts=contracts,
        verbose=False,
    )


class TestFinishSessionHappyPath:
    def test_discharged_obligation_yields_empty(self):
        """Trigger fired and response eventually fired → no violations."""
        guard = _make_guard(
            ["tool `receive_request` must always be followed by `log_request`"]
        )
        guard.guard_before("receive_request")
        guard.guard_before("log_request")

        pending = guard.finish_session()
        assert pending == []

    def test_no_trigger_yields_empty(self):
        """Trigger never fired → obligation vacuously satisfied."""
        guard = _make_guard(
            ["tool `receive_request` must always be followed by `log_request`"]
        )
        guard.guard_before("something_else")

        pending = guard.finish_session()
        assert pending == []

    def test_no_contracts_at_all(self):
        """Agent with no contracts at all → finish_session is a no-op."""
        guard = _make_guard([])
        guard.guard_before("anything")
        assert guard.finish_session() == []

    def test_only_safety_contracts(self):
        """Agent with only safety contracts (no liveness) → still empty."""
        guard = _make_guard(["tool `X` at most 3 times"])
        guard.guard_before("X")
        guard.guard_before("X")
        assert guard.finish_session() == []


class TestFinishSessionViolations:
    def test_undischarged_obligation_reports_violation(self):
        """Trigger fired but response never fired → liveness violation."""
        guard = _make_guard(
            ["tool `receive_request` must always be followed by `log_request`"]
        )
        guard.guard_before("receive_request")
        # session ends without log_request

        pending = guard.finish_session()
        assert len(pending) == 1
        assert isinstance(pending[0], Verdict)
        assert pending[0].holds is False
        assert "follow" in pending[0].desc.lower() or "log_request" in pending[0].desc

    def test_multiple_triggers_single_missing_response(self):
        """Multiple triggers, never any response → still one violation."""
        guard = _make_guard(
            ["tool `receive_request` must always be followed by `log_request`"]
        )
        for _ in range(3):
            guard.guard_before("receive_request")

        pending = guard.finish_session()
        # The G(trigger -> F(response)) formula reports as one top-level
        # violation because F never became true for any pending trigger.
        assert len(pending) == 1
        assert pending[0].holds is False

    def test_violation_recorded_in_guard_violations_list(self):
        """finish_session should append to guard.violations for reporting."""
        guard = _make_guard(["tool `A` must always be followed by `B`"])
        guard.guard_before("A")
        assert len(guard.violations) == 0  # nothing during runtime

        guard.finish_session()
        assert len(guard.violations) == 1
        v = guard.violations[0]
        assert v["tool"] == "<session_end>"
        assert "liveness" in v["constraint"]
        assert v["action"] == "ESCALATED"


class TestFinishSessionIdempotency:
    def test_second_call_returns_same_result_without_double_emit(self):
        guard = _make_guard(
            ["tool `receive_request` must always be followed by `log_request`"]
        )
        guard.guard_before("receive_request")

        first = guard.finish_session()
        # Records should reflect exactly one round of violations.
        first_violation_count = len(guard.violations)

        second = guard.finish_session()
        assert first == second
        # No new entries appended on second call.
        assert len(guard.violations) == first_violation_count

    def test_reset_allows_finish_session_to_rerun(self):
        guard = _make_guard(
            ["tool `receive_request` must always be followed by `log_request`"]
        )
        guard.guard_before("receive_request")
        first = guard.finish_session()
        assert len(first) == 1

        guard.reset()
        # Fresh session — no events, no liveness firing.
        assert guard.finish_session() == []


class TestFinishSessionAssumptionGating:
    def test_failed_assumption_hides_liveness_violation(self):
        """Contract: assumption fails → liveness obligation doesn't apply."""
        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                {
                    # Assumption: some precondition never met
                    "assumption": "tool `session_start` must precede `handle_request`",
                    "guarantee": "tool `handle_request` must always be followed by `cleanup`",
                }
            ],
            verbose=False,
        )
        # Call handle_request without session_start; assumption fails.
        guard.guard_before("handle_request")

        pending = guard.finish_session()
        # Because assumption never held, the liveness obligation shouldn't
        # be reported as violated.
        assert pending == []


class TestFinishSessionRuntimeUnaffected:
    """Regression: verify runtime guard_before behavior is unchanged."""

    def test_runtime_still_skips_liveness(self):
        """During runtime, liveness formulas should still not block."""
        guard = _make_guard(["tool `A` must always be followed by `B`"])
        r = guard.guard_before("A")
        assert r.blocked is False
        # No violation reported during runtime.
        assert len(guard.violations) == 0

    def test_safety_still_blocks_during_runtime(self):
        """Safety formulas should still be enforced during runtime."""
        guard = _make_guard(["tool `X` at most 1 times"])
        guard.guard_before("X")  # at limit
        r = guard.guard_before("X")  # over limit → blocked
        assert r.blocked is True


class TestFinishSessionMixed:
    def test_mixed_safety_and_liveness(self):
        """Session with both safety and liveness contracts."""
        guard = _make_guard(
            [
                "tool `X` at most 10 times",  # safety
                "tool `A` must always be followed by `B`",  # liveness
            ]
        )
        guard.guard_before("X")
        guard.guard_before("A")
        # B never fires
        pending = guard.finish_session()
        assert len(pending) == 1
        # Safety was always fine; only liveness fires.
        assert pending[0].holds is False


class TestFinishSessionReturnTypes:
    def test_verdicts_have_formula_reference(self):
        """Returned Verdicts should carry the original DetFormula for reporting."""
        guard = _make_guard(["tool `A` must always be followed by `B`"])
        guard.guard_before("A")
        pending = guard.finish_session()
        assert len(pending) == 1
        v = pending[0]
        assert v.formula is not None
        # The wrapped formula should be a DetFormula with liveness=True
        assert getattr(v.formula, "liveness", False) is True


class TestFinishSessionSpanEmission:
    """finish_session must produce the same span tree shape as runtime checks
    so OTEL exporters, dashboard, and render_tree all work unchanged."""

    def test_span_tree_appears_in_check_spans(self):
        """Session-end spans should be appended to ``guard.check_spans``."""
        guard = _make_guard(["tool `A` must always be followed by `B`"])
        guard.guard_before("A")
        runtime_span_count = len(guard.check_spans)

        guard.finish_session()

        assert len(guard.check_spans) == runtime_span_count + 1
        session_span = guard.check_spans[-1]
        assert session_span.action == "<session_end>"
        assert session_span.agent_id == "bot"

    def test_last_check_span_points_at_session_end(self):
        guard = _make_guard(["tool `A` must always be followed by `B`"])
        guard.guard_before("A")
        guard.finish_session()
        span = guard.last_check_span
        assert span is not None
        assert span.action == "<session_end>"

    def test_span_tree_has_correct_shape_on_violation(self):
        """AgentTurnSpan → ContractCheckSpan → GuaranteeSpan(violated)
        → ViolationSpan + EnforcementSpan. Same shape as runtime."""
        from sponsio.models.spans import (
            ContractCheckSpan,
            EnforcementSpan,
            GuaranteeSpan,
            ViolationSpan,
        )

        guard = _make_guard(["tool `A` must always be followed by `B`"])
        guard.guard_before("A")
        guard.finish_session()

        root = guard.last_check_span
        assert root.status == "violated"
        assert root.det_violations == 1
        assert root.total_contracts_checked == 1

        # Level 1: contract check
        contract_spans = [c for c in root.children if isinstance(c, ContractCheckSpan)]
        assert len(contract_spans) == 1
        contract_span = contract_spans[0]
        assert contract_span.status == "violated"

        # Level 2: guarantee span (the liveness formula, failed)
        guarantee_spans = [
            c for c in contract_span.children if isinstance(c, GuaranteeSpan)
        ]
        assert len(guarantee_spans) == 1
        g_span = guarantee_spans[0]
        assert g_span.result is False
        assert g_span.status == "violated"

        # Level 2: violation + enforcement siblings on the contract
        violation_spans = [
            c for c in contract_span.children if isinstance(c, ViolationSpan)
        ]
        enforcement_spans = [
            c for c in contract_span.children if isinstance(c, EnforcementSpan)
        ]
        assert len(violation_spans) == 1
        assert len(enforcement_spans) == 1
        assert violation_spans[0].kind == "liveness"
        assert enforcement_spans[0].strategy == "LivenessEscalate"
        assert enforcement_spans[0].result_action == "escalated"

    def test_span_tree_on_pass(self):
        """Happy path: liveness discharged → span tree still emitted but ok."""
        guard = _make_guard(["tool `A` must always be followed by `B`"])
        guard.guard_before("A")
        guard.guard_before("B")
        guard.finish_session()

        root = guard.last_check_span
        assert root is not None
        assert root.action == "<session_end>"
        assert root.status == "ok"
        assert root.det_violations == 0

    def test_no_span_emitted_when_no_liveness_contracts(self):
        """Agent with only safety contracts shouldn't produce a session-end span."""
        guard = _make_guard(["tool `X` at most 3 times"])
        guard.guard_before("X")
        runtime_span_count = len(guard.check_spans)
        guard.finish_session()
        # No change: no session-end span produced when there's nothing
        # to session-end-check.
        assert len(guard.check_spans) == runtime_span_count

    def test_render_tree_works_on_session_end_span(self):
        """The existing render_tree() helper should handle the new span tree."""
        from sponsio.models.spans import render_tree

        guard = _make_guard(["tool `A` must always be followed by `B`"])
        guard.guard_before("A")
        guard.finish_session()

        text = render_tree(guard.last_check_span, colorize=False)
        assert "<session_end>" in text
        assert "VIOLATED" in text or "violated" in text.lower()


class TestFinishSessionOTelExport:
    """Session-end violations must reach OTEL exporters the same way
    runtime violations do."""

    def test_otel_exporter_receives_session_end_span(self):
        """The configured otel_exporter.export() should be called with
        the session-end AgentTurnSpan."""
        import sponsio
        from sponsio.models.spans import AgentTurnSpan

        exported_spans: list = []

        class FakeExporter:
            def export(self, span):
                exported_spans.append(span)

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=["tool `A` must always be followed by `B`"],
            otel_exporter=FakeExporter(),
            verbose=False,
        )
        guard.guard_before("A")
        before_runtime = len(exported_spans)

        guard.finish_session()

        # At least one new span was exported after finish_session.
        assert len(exported_spans) > before_runtime
        last_exported = exported_spans[-1]
        assert isinstance(last_exported, AgentTurnSpan)
        assert last_exported.action == "<session_end>"
        assert last_exported.det_violations == 1

    def test_otel_not_called_when_exporter_missing(self):
        """Without an otel_exporter, finish_session shouldn't error."""
        guard = _make_guard(["tool `A` must always be followed by `B`"])
        guard.guard_before("A")
        # Should not raise even though no exporter is configured.
        pending = guard.finish_session()
        assert len(pending) == 1
