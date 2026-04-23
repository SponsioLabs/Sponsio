"""Unit tests for span dataclasses, SpanCollector, and render_tree."""

import time

from sponsio.models.spans import (
    AgentTurnSpan,
    ContractCheckSpan,
    EnforcementSpan,
    GuaranteeSpan,
    PreconditionSpan,
    StoCheckSpan,
    StoEvalSpan,
    Span,
    SpanCollector,
    ViolationSpan,
    render_tree,
)


# =============================================================================
# Span basics
# =============================================================================


class TestSpan:
    def test_duration_ms_none_when_not_finished(self):
        s = Span(span_type="test", start_time=time.monotonic())
        assert s.duration_ms is None

    def test_duration_ms_populated_after_finish(self):
        s = Span(span_type="test", start_time=time.monotonic())
        s.finish()
        assert s.duration_ms is not None
        assert s.duration_ms >= 0

    def test_finish_sets_status(self):
        s = Span(span_type="test", start_time=time.monotonic())
        s.finish("violated")
        assert s.status == "violated"
        assert s.end_time is not None

    def test_finish_preserves_default_status(self):
        s = Span(span_type="test", start_time=time.monotonic())
        s.finish()
        assert s.status == "ok"

    def test_to_dict_basic(self):
        s = Span(span_type="test", start_time=1.0, end_time=1.5, status="ok")
        d = s.to_dict()
        assert d["span_type"] == "test"
        assert d["start_time"] == 1.0
        assert d["end_time"] == 1.5
        assert d["duration_ms"] == 500.0
        assert d["status"] == "ok"
        assert "children" not in d
        assert "attributes" not in d

    def test_to_dict_with_attributes(self):
        s = Span(
            span_type="test",
            start_time=0,
            attributes={"key": "value"},
        )
        d = s.to_dict()
        assert d["attributes"] == {"key": "value"}

    def test_to_dict_recursive(self):
        parent = Span(span_type="parent", start_time=0, end_time=1)
        child = Span(span_type="child", start_time=0, end_time=0.5)
        parent.children.append(child)
        d = parent.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["span_type"] == "child"

    def test_to_flat_list(self):
        root = Span(span_type="root", start_time=0, end_time=1)
        c1 = Span(span_type="c1", start_time=0, end_time=0.5)
        c2 = Span(span_type="c2", start_time=0.5, end_time=1)
        gc = Span(span_type="gc", start_time=0.5, end_time=0.7)
        c2.children.append(gc)
        root.children = [c1, c2]

        flat = root.to_flat_list()
        assert len(flat) == 4
        assert [f["span_type"] for f in flat] == ["root", "c1", "c2", "gc"]

    def test_walk(self):
        root = Span(span_type="root", start_time=0)
        c1 = Span(span_type="c1", start_time=0)
        c2 = Span(span_type="c2", start_time=0)
        gc = Span(span_type="gc", start_time=0)
        c1.children.append(gc)
        root.children = [c1, c2]

        walked = list(root.walk())
        assert len(walked) == 4
        assert walked[0].span_type == "root"
        assert walked[1].span_type == "c1"
        assert walked[2].span_type == "gc"
        assert walked[3].span_type == "c2"


# =============================================================================
# Typed span subclasses
# =============================================================================


class TestTypedSpans:
    def test_agent_turn_span_defaults(self):
        s = AgentTurnSpan(span_type="sponsio.agent_turn", start_time=0)
        assert s.agent_id == ""
        assert s.action == ""
        assert s.total_contracts_checked == 0
        assert s.det_violations == 0
        assert s.sto_violations == 0
        assert s.blocked is False

    def test_contract_check_span(self):
        s = ContractCheckSpan(
            span_type="sponsio.contract_check",
            start_time=0,
            contract_name="must_precede",
            pipeline="det",
        )
        assert s.contract_name == "must_precede"
        assert s.pipeline == "det"

    def test_precondition_span(self):
        s = PreconditionSpan(
            span_type="sponsio.precondition",
            start_time=0,
            formula_desc="session_authenticated",
            result=True,
        )
        assert s.formula_desc == "session_authenticated"
        assert s.result is True

    def test_guarantee_span(self):
        s = GuaranteeSpan(
            span_type="sponsio.guarantee",
            start_time=0,
            formula_desc="called(lookup) before called(refund)",
            result=False,
        )
        assert s.result is False

    def test_violation_span(self):
        s = ViolationSpan(
            span_type="sponsio.violation",
            start_time=0,
            kind="guarantee",
            severity="HIGH",
            evidence="lookup not found",
        )
        assert s.kind == "guarantee"
        assert s.severity == "HIGH"

    def test_enforcement_span(self):
        s = EnforcementSpan(
            span_type="sponsio.enforcement",
            start_time=0,
            strategy="DetBlock",
            result_action="blocked",
        )
        assert s.strategy == "DetBlock"
        assert s.result_action == "blocked"

    def test_soft_eval_span(self):
        s = StoEvalSpan(
            span_type="sponsio.sto_eval",
            start_time=0,
            constraint_name="tone",
            score=0.3,
            threshold=0.5,
            passed=False,
        )
        assert s.constraint_name == "tone"
        assert s.score == 0.3
        assert s.passed is False


# =============================================================================
# SpanCollector
# =============================================================================


class TestSpanCollector:
    def test_context_manager_sets_root(self):
        with SpanCollector("agent_1", "do_thing") as c:
            pass
        assert c.root.span_type == "sponsio.agent_turn"
        assert c.root.agent_id == "agent_1"
        assert c.root.action == "do_thing"
        assert c.root.end_time is not None

    def test_start_and_finish_span(self):
        with SpanCollector("a", "x") as c:
            c.start_contract_check("test_contract")
            c.finish_span("ok")
        assert len(c.root.children) == 1
        child = c.root.children[0]
        assert isinstance(child, ContractCheckSpan)
        assert child.contract_name == "test_contract"
        assert child.status == "ok"
        assert child.end_time is not None

    def test_nested_spans(self):
        with SpanCollector("a", "x") as c:
            c.start_contract_check("contract_1")
            c.start_precondition("auth_ok")
            c.finish_span("ok")
            c.start_guarantee("must_precede")
            c.finish_span("ok")
            c.finish_span("ok")  # close contract_check

        assert len(c.root.children) == 1
        contract = c.root.children[0]
        assert len(contract.children) == 2
        assert isinstance(contract.children[0], PreconditionSpan)
        assert isinstance(contract.children[1], GuaranteeSpan)

    def test_add_violation_does_not_push(self):
        with SpanCollector("a", "x") as c:
            c.start_contract_check("c1")
            c.add_violation(kind="guarantee", evidence="missing precondition")
            # Current should still be contract_check, not violation
            assert c.current.span_type == "sponsio.contract_check"
            c.finish_span()

        violation = c.root.children[0].children[0]
        assert isinstance(violation, ViolationSpan)
        assert violation.status == "violated"

    def test_add_enforcement_does_not_push(self):
        with SpanCollector("a", "x") as c:
            c.start_contract_check("c1")
            c.add_enforcement(strategy="DetBlock", result_action="blocked")
            assert c.current.span_type == "sponsio.contract_check"
            c.finish_span()

        enforcement = c.root.children[0].children[0]
        assert isinstance(enforcement, EnforcementSpan)
        assert enforcement.strategy == "DetBlock"

    def test_soft_pipeline_spans(self):
        with SpanCollector("a", "x") as c:
            c.start_sto_check()
            c.start_sto_eval("tone", score=0.8, threshold=0.5, passed=True)
            c.finish_span("ok")
            c.start_sto_eval("pii", score=0.2, threshold=0.5, passed=False)
            c.add_violation(kind="sto", evidence="contains SSN")
            c.finish_span("violated")
            c.finish_span()  # close soft_check

        soft_check = c.root.children[0]
        assert isinstance(soft_check, StoCheckSpan)
        assert len(soft_check.children) == 2

        tone = soft_check.children[0]
        assert isinstance(tone, StoEvalSpan)
        assert tone.passed is True

        pii = soft_check.children[1]
        assert isinstance(pii, StoEvalSpan)
        assert pii.passed is False
        assert len(pii.children) == 1  # violation child

    def test_finish_span_underflow_raises(self):
        """#15: ``finish_span`` used to silently return the root on a
        stack underflow, which let mismatched start/finish pairs corrupt
        the whole span tree without any signal. It now raises so the
        caller bug is visible."""
        import pytest

        with SpanCollector("a", "x") as c:
            with pytest.raises(RuntimeError, match="stack underflow"):
                c.finish_span()

    def test_current_property(self):
        with SpanCollector("a", "x") as c:
            assert c.current is c.root
            c.start_contract_check("c1")
            assert c.current.span_type == "sponsio.contract_check"
            c.finish_span()
            assert c.current is c.root


# =============================================================================
# render_tree
# =============================================================================


class TestRenderTree:
    def _make_simple_tree(self) -> AgentTurnSpan:
        """Build a small tree for rendering tests."""
        root = AgentTurnSpan(
            span_type="sponsio.agent_turn",
            start_time=0,
            end_time=0.023,
            agent_id="bot",
            action="process_refund",
        )
        cc = ContractCheckSpan(
            span_type="sponsio.contract_check",
            start_time=0.001,
            end_time=0.020,
            contract_name="must_precede(lookup → refund)",
        )
        pc = PreconditionSpan(
            span_type="sponsio.precondition",
            start_time=0.002,
            end_time=0.003,
            formula_desc="session_authenticated",
            result=True,
        )
        gg = GuaranteeSpan(
            span_type="sponsio.guarantee",
            start_time=0.004,
            end_time=0.009,
            status="violated",
            formula_desc="called(lookup) before called(refund)",
            result=False,
        )
        vv = ViolationSpan(
            span_type="sponsio.violation",
            start_time=0.010,
            end_time=0.010,
            status="violated",
            kind="guarantee",
            severity="HIGH",
            evidence="lookup not in trace",
        )
        ee = EnforcementSpan(
            span_type="sponsio.enforcement",
            start_time=0.011,
            end_time=0.013,
            strategy="DetBlock",
            result_action="blocked",
        )
        cc.children = [pc, gg, vv, ee]
        root.children = [cc]
        return root

    def test_render_tree_no_color(self):
        tree = self._make_simple_tree()
        output = render_tree(tree, colorize=False)
        lines = output.strip().split("\n")
        assert len(lines) >= 5
        # Root line should mention the action
        assert "bot.process_refund" in lines[0]
        # Should have the contract name
        assert "must_precede" in output
        # Should have SATISFIED for precondition
        assert "SATISFIED" in output
        # Should have VIOLATED for guarantee
        assert "VIOLATED" in output
        # Should have DetBlock
        assert "DetBlock" in output

    def test_render_tree_with_color(self):
        tree = self._make_simple_tree()
        output = render_tree(tree, colorize=True)
        # Should contain ANSI escape codes
        assert "\033[" in output

    def test_render_tree_empty_root(self):
        root = AgentTurnSpan(
            span_type="sponsio.agent_turn",
            start_time=0,
            end_time=0.001,
            agent_id="a",
            action="x",
        )
        output = render_tree(root, colorize=False)
        assert "a.x" in output
        # Only one line (root, no children)
        assert len(output.strip().split("\n")) == 1

    def test_render_tree_timing(self):
        root = AgentTurnSpan(
            span_type="sponsio.agent_turn",
            start_time=0,
            end_time=0.050,  # 50ms
            agent_id="a",
            action="x",
        )
        output = render_tree(root, colorize=False)
        assert "50ms" in output
