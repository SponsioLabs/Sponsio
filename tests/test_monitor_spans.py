"""Integration tests: RuntimeMonitor + BaseGuard produce correct span trees."""

from sponsio.integrations.langgraph import LangGraphGuard
from sponsio.models.spans import (
    AgentTurnSpan,
    ContractCheckSpan,
    EnforcementSpan,
    GuaranteeSpan,
    ViolationSpan,
)


# =============================================================================
# Det pipeline spans
# =============================================================================


class TestHardPipelineSpans:
    def test_passing_check_produces_ok_tree(self):
        """A passing contract check → AgentTurnSpan → ContractCheckSpan → GuaranteeSpan(ok)."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("check_policy")
        result = guard.pre_check("issue_refund")
        assert result.allowed

        span = guard.last_check_span
        assert span is not None
        assert isinstance(span, AgentTurnSpan)
        assert span.agent_id == "bot"
        assert span.action == "issue_refund"
        assert span.blocked is False
        assert span.end_time is not None
        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_violation_produces_full_tree(self):
        """A det violation → ViolationSpan + EnforcementSpan children."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        result = guard.pre_check("issue_refund")
        assert result.blocked

        span = guard.last_check_span
        assert span is not None
        assert span.blocked is True
        assert span.det_violations >= 1

        # Find the contract check span
        contract_spans = [
            c
            for c in span.children
            if isinstance(c, ContractCheckSpan) and c.pipeline == "det"
        ]
        assert len(contract_spans) >= 1

        # The contract check should have guarantee + violation + enforcement children
        cc = contract_spans[0]
        child_types = [type(c) for c in cc.children]
        assert GuaranteeSpan in child_types
        assert ViolationSpan in child_types
        assert EnforcementSpan in child_types

        # Verify violation details
        violation = next(c for c in cc.children if isinstance(c, ViolationSpan))
        assert violation.kind == "guarantee"
        assert violation.severity == "HIGH"
        assert violation.status == "violated"

        # Verify enforcement details
        enforcement = next(c for c in cc.children if isinstance(c, EnforcementSpan))
        assert enforcement.strategy == "DetBlock"
        assert enforcement.result_action == "blocked"

    def test_multiple_contracts_produce_sibling_spans(self):
        """Multiple contracts → sibling ContractCheckSpan nodes."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=[
                "tool `check_policy` must precede `issue_refund`",
                "tool `issue_refund` must not be called more than 5 times per session",
            ],
        )
        guard.pre_check("check_policy")
        guard.pre_check("issue_refund")

        span = guard.last_check_span
        assert span is not None

        # Should have at least one contract check child
        contract_checks = [c for c in span.children if isinstance(c, ContractCheckSpan)]
        assert len(contract_checks) >= 1

    def test_last_turn_span_updates_per_call(self):
        """last_turn_span is always the most recent check."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("check_policy")
        span1 = guard.last_check_span
        assert span1 is not None
        assert span1.action == "check_policy"

        guard.pre_check("other_tool")
        span2 = guard.last_check_span
        assert span2 is not None
        assert span2.action == "other_tool"
        assert span2 is not span1

    def test_check_spans_accumulate(self):
        """check_spans accumulates across calls."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("check_policy")
        guard.pre_check("other_tool")

        spans = guard.check_spans
        assert len(spans) == 2
        assert spans[0].action == "check_policy"
        assert spans[1].action == "other_tool"


# =============================================================================
# Sto pipeline spans
# =============================================================================


class TestMonitorSpanProperties:
    def test_monitor_last_turn_span(self):
        """RuntimeMonitor.last_turn_span is populated after check_action()."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        monitor = guard.monitor

        assert monitor.last_turn_span is None
        guard.pre_check("check_policy")
        assert monitor.last_turn_span is not None

    def test_monitor_turn_spans_accumulate(self):
        """RuntimeMonitor.turn_spans grows with each check_action()."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        monitor = guard.monitor

        guard.pre_check("check_policy")
        guard.pre_check("other")
        assert len(monitor.turn_spans) == 2

    def test_reset_clears_spans(self):
        """reset() clears both last_turn_span and turn_spans."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        monitor = guard.monitor

        guard.pre_check("check_policy")
        assert monitor.last_turn_span is not None
        assert len(monitor.turn_spans) == 1

        guard.reset()
        assert monitor.last_turn_span is None
        assert len(monitor.turn_spans) == 0

    def test_durations_non_negative(self):
        """All span durations should be non-negative."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("issue_refund")

        span = guard.last_check_span
        assert span is not None
        for s in span.walk():
            if s.duration_ms is not None:
                assert s.duration_ms >= 0, f"{s.span_type} has negative duration"

    def test_render_last_turn(self):
        """monitor.render_last_turn() produces non-empty output."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("issue_refund")

        output = guard.monitor.render_last_turn(colorize=False)
        assert len(output) > 0
        assert "bot" in output

    def test_render_last_turn_empty_when_no_spans(self):
        guard = LangGraphGuard(agent_id="bot")
        assert guard.monitor.render_last_turn() == ""


# =============================================================================
# BaseGuard span accessors
# =============================================================================


class TestBaseGuardSpanAccessors:
    def test_last_check_span_property(self):
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        assert guard.last_check_span is None
        guard.pre_check("check_policy")
        assert guard.last_check_span is not None
        assert isinstance(guard.last_check_span, AgentTurnSpan)

    def test_check_spans_property(self):
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("check_policy")
        guard.pre_check("issue_refund")
        assert len(guard.check_spans) == 2

    def test_render_checks(self):
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("check_policy")
        guard.pre_check("issue_refund")

        output = guard.render_checks(colorize=False)
        assert len(output) > 0
        # Should contain both turns
        assert "check_policy" in output
        assert "issue_refund" in output

    def test_render_checks_empty(self):
        guard = LangGraphGuard(agent_id="bot")
        assert guard.render_checks() == ""

    def test_span_tree_serialization_roundtrip(self):
        """to_dict() on a real span tree produces valid JSON-serializable output."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("issue_refund")

        span = guard.last_check_span
        assert span is not None
        d = span.to_dict()

        # Should be JSON-serializable
        import json

        json_str = json.dumps(d)
        assert len(json_str) > 0

        # Roundtrip preserves structure
        parsed = json.loads(json_str)
        assert parsed["span_type"] == "sponsio.agent_turn"
        assert "children" in parsed

    def test_flat_list_from_real_tree(self):
        """to_flat_list() on a real violation tree captures all spans."""
        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        guard.pre_check("issue_refund")

        span = guard.last_check_span
        assert span is not None
        flat = span.to_flat_list()

        # Root + contract_check + guarantee + violation + enforcement = 5+
        assert len(flat) >= 4
        types = [f["span_type"] for f in flat]
        assert "sponsio.agent_turn" in types
        assert "sponsio.contract_check" in types
