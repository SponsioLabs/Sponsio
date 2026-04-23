"""OTEL ingestion endpoints — receive traces from any OTEL-compatible source.

Makes the Sponsio dashboard an OTEL backend. Any agent framework that
exports OTLP JSON traces can send them here alongside Sponsio's own
contract enforcement spans.

Endpoints:
    POST /v1/traces — receive OTLP JSON payload
    GET  /traces    — list trace summaries
    GET  /traces/{trace_id}      — full span tree for one trace
    GET  /traces/{trace_id}/flat — flat span list for one trace
    DELETE /traces  — clear all stored traces
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from api.state import state
from api.trace_store import TraceStore
from sponsio.tracer.otel_consumer import otel_to_trace

router = APIRouter()

# Shared store instance — imported by main.py and monitor.py bridge
trace_store = TraceStore()


@router.post("/v1/traces")
async def ingest_traces(payload: dict, sync_monitor: bool = False):
    """Receive OTLP JSON traces from any OTEL SDK."""
    resource_spans = payload.get("resourceSpans", [])
    count = trace_store.ingest(resource_spans)
    response = {"status": "ok", "spans_received": count}
    if sync_monitor:
        trace = otel_to_trace(payload)
        state.monitor.import_trace(trace)
        response["native_events_imported"] = len(trace.events)
    return response


@router.get("/traces")
def list_traces(
    limit: int = 50,
    has_violations: Optional[bool] = None,
):
    """List trace summaries, newest first."""
    return trace_store.list_traces(limit=limit, has_violations=has_violations)


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str):
    """Get full span tree for one trace."""
    result = trace_store.get_trace_tree(trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return result


@router.get("/traces/{trace_id}/flat")
def get_trace_flat(trace_id: str):
    """Get flat span list for one trace, sorted by start time."""
    result = trace_store.get_trace_flat(trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return {"trace_id": trace_id, "spans": result}


@router.delete("/traces")
def clear_traces():
    """Delete all stored traces."""
    trace_store.clear()
    return {"status": "cleared"}
