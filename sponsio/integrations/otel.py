"""OpenTelemetry span exporter for Sponsio contract enforcement traces.

Translates Sponsio's internal span trees (rooted at ``AgentTurnSpan``)
into OpenTelemetry spans and sends them to any OTEL-compatible backend
(LangFuse, Arize Phoenix, Datadog, Jaeger, etc.).

**Optional dependency** — requires ``pip install sponsio[otel]``.

Usage::

    from sponsio.integrations.otel import OTelExporter
    from sponsio.integrations.langgraph import LangGraphGuard

    exporter = OTelExporter(
        endpoint="https://us.cloud.langfuse.com/api/public/otel/v1/traces",
        headers={"Authorization": "Basic <base64(PK:SK)>"},
    )

    # Auto-export: every guard_before/guard_after pushes spans
    guard = LangGraphGuard(
        contracts=["tool `check_policy` must precede `issue_refund`"],
        otel_exporter=exporter,
    )

    # Or manual export:
    exporter.export(guard.last_check_span)
    exporter.shutdown()
"""

from __future__ import annotations

import time
from typing import Any, Optional

from sponsio.models.spans import (
    AgentTurnSpan,
    ContractCheckSpan,
    EnforcementSpan,
    GuaranteeSpan,
    PreconditionSpan,
    StoEvalSpan,
    Span,
    ViolationSpan,
)

try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.trace import StatusCode
except ImportError:
    raise ImportError(
        "OpenTelemetry dependencies not installed. "
        "Install with: pip install sponsio[otel]"
    )


class OTelExporter:
    """Export Sponsio span trees to any OTEL-compatible backend.

    Args:
        endpoint: OTLP HTTP endpoint URL (e.g. ``https://host/v1/traces``).
        headers: Optional HTTP headers (e.g. auth tokens).
        service_name: Service name reported to the OTEL backend.
        tracer_provider: Optional pre-configured TracerProvider.
            If provided, ``endpoint``, ``headers``, and ``service_name``
            are ignored.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:4318/v1/traces",
        headers: Optional[dict[str, str]] = None,
        service_name: str = "sponsio",
        tracer_provider: Optional[TracerProvider] = None,
    ) -> None:
        if tracer_provider is not None:
            self._provider = tracer_provider
            self._owns_provider = False
        else:
            resource = Resource.create({"service.name": service_name})
            self._provider = TracerProvider(resource=resource)
            otel_exporter = OTLPSpanExporter(
                endpoint=endpoint,
                headers=headers or {},
            )
            self._provider.add_span_processor(BatchSpanProcessor(otel_exporter))
            self._owns_provider = True

        self._tracer = self._provider.get_tracer("sponsio", "0.1.0")

    def export(
        self,
        root: AgentTurnSpan,
        parent_context: Optional[Any] = None,
    ) -> None:
        """Export a Sponsio span tree as OTEL spans.

        Recursively walks the tree. Each Sponsio span becomes one OTEL
        span with parent-child nesting preserved.

        Args:
            root: The root ``AgentTurnSpan`` from a ``check_action()`` call.
            parent_context: Optional OTEL context to nest under (e.g. from
                a LangGraph trace). If None, spans are top-level.
        """
        if root is None:
            return
        # Compute monotonic → wall-clock offset once per export
        mono_now = time.monotonic()
        wall_now = time.time()
        offset = wall_now - mono_now
        ctx = parent_context or otel_context.get_current()
        self._export_span(root, ctx, offset)

    def shutdown(self) -> None:
        """Flush pending spans and shut down the provider."""
        if self._owns_provider:
            self._provider.shutdown()

    def force_flush(self, timeout_millis: int = 5000) -> None:
        """Force flush any pending spans."""
        if self._owns_provider:
            self._provider.force_flush(timeout_millis)

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    def _export_span(self, span: Span, parent_ctx: Any, offset: float) -> None:
        """Recursively create OTEL spans for a Sponsio span and its children."""
        name = span.span_type
        attrs = self._extract_attributes(span)
        status = self._map_status(span.status)

        # Convert monotonic timestamps to wall-clock nanoseconds
        start_ns = int((offset + span.start_time) * 1e9)
        end_ns = (
            int((offset + span.end_time) * 1e9)
            if span.end_time is not None
            else int(time.time() * 1e9)
        )

        otel_span = self._tracer.start_span(
            name=name,
            context=parent_ctx,
            attributes=attrs,
            start_time=start_ns,
        )
        otel_span.set_status(status)
        otel_span.end(end_time=end_ns)

        # Build child context from this span
        child_ctx = trace.set_span_in_context(otel_span, parent_ctx)

        for child in span.children:
            self._export_span(child, child_ctx, offset)

    @staticmethod
    def _extract_attributes(span: Span) -> dict[str, Any]:
        """Map Sponsio span fields to OTEL attributes."""
        attrs: dict[str, Any] = {}

        if isinstance(span, AgentTurnSpan):
            attrs["sponsio.agent_id"] = span.agent_id
            attrs["sponsio.action"] = span.action
            attrs["sponsio.blocked"] = span.blocked
            attrs["sponsio.det_violations"] = span.det_violations
            attrs["sponsio.sto_violations"] = span.sto_violations
            attrs["sponsio.total_contracts_checked"] = span.total_contracts_checked

        elif isinstance(span, ContractCheckSpan):
            attrs["sponsio.contract.name"] = span.contract_name
            attrs["sponsio.contract.pipeline"] = span.pipeline

        elif isinstance(span, PreconditionSpan):
            attrs["sponsio.formula"] = span.formula_desc
            attrs["sponsio.result"] = span.result

        elif isinstance(span, GuaranteeSpan):
            attrs["sponsio.formula"] = span.formula_desc
            attrs["sponsio.result"] = span.result

        elif isinstance(span, ViolationSpan):
            attrs["sponsio.violation.kind"] = span.kind
            attrs["sponsio.violation.severity"] = span.severity
            attrs["sponsio.violation.evidence"] = span.evidence

        elif isinstance(span, EnforcementSpan):
            attrs["sponsio.enforcement.strategy"] = span.strategy
            attrs["sponsio.enforcement.action"] = span.result_action

        elif isinstance(span, StoEvalSpan):
            attrs["sponsio.sto.constraint"] = span.constraint_name
            attrs["sponsio.sto.score"] = span.score
            attrs["sponsio.sto.threshold"] = span.threshold
            attrs["sponsio.sto.passed"] = span.passed

        # StoCheckSpan has no extra attributes

        return attrs

    @staticmethod
    def _map_status(status: str) -> StatusCode:
        """Map Sponsio status string to OTEL StatusCode."""
        if status in ("violated", "error"):
            return StatusCode.ERROR
        return StatusCode.OK
