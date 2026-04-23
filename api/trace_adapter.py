"""Trace payload adapters for the API layer."""

from __future__ import annotations

from api.schemas import TraceImportRequest
from sponsio.models.trace import Trace
from sponsio.tracer.otel_consumer import otel_to_trace


def is_otlp_payload(data: dict) -> bool:
    """Return True when *data* looks like an OTLP JSON trace export."""
    return isinstance(data.get("resourceSpans"), list)


def trace_from_import_payload(data: dict) -> Trace:
    """Convert either native Sponsio trace JSON or OTLP JSON into a Trace."""
    if is_otlp_payload(data):
        return otel_to_trace(data)

    req = TraceImportRequest.model_validate(data)
    return Trace.from_dict(
        {"events": [e.model_dump() for e in req.events], "metadata": req.metadata}
    )
