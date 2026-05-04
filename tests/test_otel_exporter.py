"""Tests for the OTEL span exporter."""

from __future__ import annotations

import time

import pytest

from sponsio.models.spans import (
    AgentTurnSpan,
    ContractCheckSpan,
    EnforcementSpan,
    GuaranteeSpan,
    PreconditionSpan,
    StoCheckSpan,
    StoEvalSpan,
    ViolationSpan,
)

try:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        SimpleSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )
    from opentelemetry import trace
    from sponsio.integrations.otel import OTelExporter

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

pytestmark = pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed")

if not HAS_OTEL:
    pytest.skip("opentelemetry not installed", allow_module_level=True)


class InMemorySpanExporter(SpanExporter):
    """Simple in-memory exporter for testing."""

    def __init__(self):
        self._spans = []
        self._stopped = False

    def export(self, spans):
        if self._stopped:
            return SpanExportResult.FAILURE
        self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self._stopped = True

    def force_flush(self, timeout_millis=0):
        return True

    def get_finished_spans(self):
        return list(self._spans)

    def clear(self):
        self._spans.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider_and_memory():
    """Create a TracerProvider with InMemorySpanExporter for assertions."""
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    return provider, memory


def _build_sample_tree() -> AgentTurnSpan:
    """Build a realistic span tree manually."""
    now = time.monotonic()

    root = AgentTurnSpan(
        span_type="sponsio.agent_turn",
        start_time=now,
        end_time=now + 0.1,
        status="violated",
        agent_id="customer_bot",
        action="issue_refund",
        total_contracts_checked=1,
        det_violations=1,
        sto_violations=0,
        blocked=True,
    )

    # Contract check
    contract = ContractCheckSpan(
        span_type="sponsio.contract_check",
        start_time=now + 0.01,
        end_time=now + 0.08,
        status="violated",
        contract_name="check_policy must precede issue_refund",
        pipeline="det",
    )

    # Precondition (satisfied)
    pre = PreconditionSpan(
        span_type="sponsio.precondition",
        start_time=now + 0.02,
        end_time=now + 0.03,
        status="ok",
        formula_desc="agent is customer_bot",
        result=True,
    )

    # Guarantee (violated)
    guar = GuaranteeSpan(
        span_type="sponsio.enforcement",
        start_time=now + 0.03,
        end_time=now + 0.05,
        status="violated",
        formula_desc="check_policy must precede issue_refund",
        result=False,
    )

    # Violation
    viol = ViolationSpan(
        span_type="sponsio.violation",
        start_time=now + 0.05,
        end_time=now + 0.055,
        status="violated",
        kind="guarantee",
        severity="HIGH",
        evidence="issue_refund called without prior check_policy",
    )

    # Enforcement
    enf = EnforcementSpan(
        span_type="sponsio.guarantee",
        start_time=now + 0.055,
        end_time=now + 0.06,
        status="ok",
        strategy="DetBlock",
        result_action="blocked",
    )

    contract.children = [pre, guar, viol, enf]

    # Sto pipeline
    soft_check = StoCheckSpan(
        span_type="sponsio.sto_check",
        start_time=now + 0.06,
        end_time=now + 0.09,
        status="ok",
    )

    soft_eval = StoEvalSpan(
        span_type="sponsio.sto_eval",
        start_time=now + 0.07,
        end_time=now + 0.085,
        status="ok",
        constraint_name="tone_empathy",
        score=0.85,
        threshold=0.70,
        passed=True,
    )

    soft_check.children = [soft_eval]
    root.children = [contract, soft_check]

    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOTelExporter:
    """Tests for OTelExporter."""

    def test_export_span_count(self):
        """Correct number of OTEL spans created from the tree."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        spans = memory.get_finished_spans()
        # root(1) + contract(1) + pre(1) + guar(1) + viol(1) + enf(1) + soft_check(1) + soft_eval(1) = 8
        assert len(spans) == 8

    def test_span_names(self):
        """Each OTEL span has the correct name from span_type."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        names = {s.name for s in memory.get_finished_spans()}
        expected = {
            "sponsio.agent_turn",
            "sponsio.contract_check",
            "sponsio.precondition",
            "sponsio.enforcement",
            "sponsio.violation",
            "sponsio.guarantee",
            "sponsio.sto_check",
            "sponsio.sto_eval",
        }
        assert names == expected

    def test_parent_child_relationships(self):
        """Parent-child nesting is preserved via OTEL span IDs."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        spans = memory.get_finished_spans()
        by_name = {s.name: s for s in spans}

        root_span = by_name["sponsio.agent_turn"]
        contract_span = by_name["sponsio.contract_check"]
        guarantee_span = by_name["sponsio.guarantee"]
        soft_check_span = by_name["sponsio.sto_check"]
        soft_eval_span = by_name["sponsio.sto_eval"]

        # contract_check is child of agent_turn
        assert contract_span.parent.span_id == root_span.context.span_id
        # guarantee is child of contract_check
        assert guarantee_span.parent.span_id == contract_span.context.span_id
        # soft_check is child of agent_turn
        assert soft_check_span.parent.span_id == root_span.context.span_id
        # soft_eval is child of soft_check
        assert soft_eval_span.parent.span_id == soft_check_span.context.span_id

    def test_agent_turn_attributes(self):
        """AgentTurnSpan attributes are correctly mapped."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        span = next(
            s for s in memory.get_finished_spans() if s.name == "sponsio.agent_turn"
        )
        assert span.attributes["sponsio.agent_id"] == "customer_bot"
        assert span.attributes["sponsio.action"] == "issue_refund"
        assert span.attributes["sponsio.blocked"] is True
        assert span.attributes["sponsio.det_violations"] == 1
        assert span.attributes["sponsio.sto_violations"] == 0
        assert span.attributes["sponsio.total_contracts_checked"] == 1

    def test_contract_check_attributes(self):
        """ContractCheckSpan attributes are correctly mapped."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        span = next(
            s for s in memory.get_finished_spans() if s.name == "sponsio.contract_check"
        )
        assert (
            span.attributes["sponsio.contract.name"]
            == "check_policy must precede issue_refund"
        )
        assert span.attributes["sponsio.contract.pipeline"] == "det"

    def test_precondition_attributes(self):
        """PreconditionSpan attributes are correctly mapped."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        span = next(
            s for s in memory.get_finished_spans() if s.name == "sponsio.precondition"
        )
        assert span.attributes["sponsio.formula"] == "agent is customer_bot"
        assert span.attributes["sponsio.result"] is True

    def test_guarantee_attributes(self):
        """GuaranteeSpan attributes are correctly mapped."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        span = next(
            s for s in memory.get_finished_spans() if s.name == "sponsio.enforcement"
        )
        assert (
            span.attributes["sponsio.formula"]
            == "check_policy must precede issue_refund"
        )
        assert span.attributes["sponsio.result"] is False

    def test_violation_attributes(self):
        """ViolationSpan attributes are correctly mapped."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        span = next(
            s for s in memory.get_finished_spans() if s.name == "sponsio.violation"
        )
        assert span.attributes["sponsio.violation.kind"] == "guarantee"
        assert span.attributes["sponsio.violation.severity"] == "HIGH"
        assert (
            span.attributes["sponsio.violation.evidence"]
            == "issue_refund called without prior check_policy"
        )

    def test_liveness_violation_kind_exported(self):
        """``finish_session`` emits ViolationSpan(kind='liveness') and the
        OTEL exporter must pass that value through unchanged so
        observability backends can distinguish runtime vs end-of-session
        violations."""
        import sponsio

        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=["tool `A` must always be followed by `B`"],
            otel_exporter=exporter,
            verbose=False,
        )
        guard.guard_before("A")
        guard.finish_session()  # should push a synthetic session-end span

        exporter.force_flush()

        violation_spans = [
            s for s in memory.get_finished_spans() if s.name == "sponsio.violation"
        ]
        # One liveness violation span should have been exported.
        liveness_spans = [
            s
            for s in violation_spans
            if s.attributes.get("sponsio.violation.kind") == "liveness"
        ]
        assert len(liveness_spans) >= 1
        lv = liveness_spans[0]
        assert lv.attributes["sponsio.violation.severity"] == "HIGH"
        assert "session end" in lv.attributes["sponsio.violation.evidence"].lower()

    def test_session_end_agent_turn_span_exported(self):
        """The synthetic ``AgentTurnSpan(action='<session_end>')`` must
        export with the correct agent.id / action / det_violations
        attributes so dashboards can group it alongside runtime turns."""
        import sponsio

        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=["tool `A` must always be followed by `B`"],
            otel_exporter=exporter,
            verbose=False,
        )
        guard.guard_before("A")
        guard.finish_session()
        exporter.force_flush()

        turn_spans = [
            s for s in memory.get_finished_spans() if s.name == "sponsio.agent_turn"
        ]
        session_end_spans = [
            s
            for s in turn_spans
            if s.attributes.get("sponsio.action") == "<session_end>"
        ]
        assert len(session_end_spans) == 1
        se = session_end_spans[0]
        assert se.attributes["sponsio.agent_id"] == "bot"
        assert se.attributes["sponsio.det_violations"] == 1
        assert se.attributes["sponsio.blocked"] is False

    def test_enforcement_attributes(self):
        """EnforcementSpan attributes are correctly mapped."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        span = next(
            s for s in memory.get_finished_spans() if s.name == "sponsio.guarantee"
        )
        assert span.attributes["sponsio.enforcement.strategy"] == "DetBlock"
        assert span.attributes["sponsio.enforcement.action"] == "blocked"

    def test_soft_eval_attributes(self):
        """StoEvalSpan attributes are correctly mapped."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        span = next(
            s for s in memory.get_finished_spans() if s.name == "sponsio.sto_eval"
        )
        assert span.attributes["sponsio.sto.constraint"] == "tone_empathy"
        assert span.attributes["sponsio.sto.score"] == 0.85
        assert span.attributes["sponsio.sto.threshold"] == 0.70
        assert span.attributes["sponsio.sto.passed"] is True

    def test_status_violated_is_error(self):
        """Violated spans have OTEL StatusCode.ERROR."""
        from opentelemetry.trace import StatusCode

        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        violated_spans = [
            s
            for s in memory.get_finished_spans()
            if s.name
            in (
                "sponsio.agent_turn",
                "sponsio.enforcement",
                "sponsio.violation",
                "sponsio.contract_check",
            )
        ]
        for s in violated_spans:
            assert s.status.status_code == StatusCode.ERROR, f"{s.name} should be ERROR"

    def test_status_ok(self):
        """Non-violated spans have OTEL StatusCode.OK."""
        from opentelemetry.trace import StatusCode

        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        root = _build_sample_tree()
        exporter.export(root)
        exporter.force_flush()

        ok_spans = [
            s
            for s in memory.get_finished_spans()
            if s.name
            in (
                "sponsio.precondition",
                "sponsio.guarantee",
                "sponsio.sto_check",
                "sponsio.sto_eval",
            )
        ]
        for s in ok_spans:
            assert s.status.status_code == StatusCode.OK, f"{s.name} should be OK"

    def test_export_none_is_noop(self):
        """Exporting None does nothing."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)
        exporter.export(None)
        exporter.force_flush()
        assert len(memory.get_finished_spans()) == 0

    def test_parent_context(self):
        """Root span nests under provided parent context."""
        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        # Create a parent span
        tracer = provider.get_tracer("test")
        parent = tracer.start_span("langgraph.pipeline")
        parent_ctx = trace.set_span_in_context(parent)
        parent.end()

        root = _build_sample_tree()
        exporter.export(root, parent_context=parent_ctx)
        exporter.force_flush()

        spans = memory.get_finished_spans()
        agent_turn = next(s for s in spans if s.name == "sponsio.agent_turn")
        # Agent turn should be child of the parent span
        assert agent_turn.parent.span_id == parent.context.span_id

    def test_baseguard_auto_export(self):
        """BaseGuard with otel_exporter auto-exports after guard_before."""
        from sponsio.integrations.base import BaseGuard

        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        guard = BaseGuard(
            agent_id="test_agent",
            contracts=["tool `a` must precede `b`"],
            otel_exporter=exporter,
        )

        guard.guard_before("a")
        exporter.force_flush()

        spans = memory.get_finished_spans()
        assert len(spans) > 0
        agent_turn = next(s for s in spans if s.name == "sponsio.agent_turn")
        assert agent_turn.attributes["sponsio.agent_id"] == "test_agent"
        assert agent_turn.attributes["sponsio.action"] == "a"

    def test_baseguard_violation_exported(self):
        """BaseGuard exports violation spans when a contract is violated."""
        from sponsio.integrations.base import BaseGuard

        provider, memory = _make_provider_and_memory()
        exporter = OTelExporter(tracer_provider=provider)

        guard = BaseGuard(
            agent_id="test_agent",
            contracts=["tool `a` must precede `b`"],
            otel_exporter=exporter,
        )

        # Call b without a — should violate
        guard.guard_before("b")
        exporter.force_flush()

        spans = memory.get_finished_spans()
        names = [s.name for s in spans]
        assert "sponsio.agent_turn" in names
        assert "sponsio.violation" in names or "sponsio.guarantee" in names

        # Check the agent_turn is marked as blocked
        agent_turn = next(s for s in spans if s.name == "sponsio.agent_turn")
        assert agent_turn.attributes["sponsio.blocked"] is True
