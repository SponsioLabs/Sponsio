"""Tests for OTEL ingestion endpoint and TraceStore."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers.otel_ingest import trace_store


@pytest.fixture(autouse=True)
def _clear_store():
    """Clear the trace store before each test."""
    trace_store.clear()
    yield
    trace_store.clear()


client = TestClient(app)


# ---------------------------------------------------------------------------
# Helper: build OTLP payloads
# ---------------------------------------------------------------------------


def _otlp_payload(
    spans: list[dict],
    scope: str = "langchain",
    service_name: str = "my-agent",
) -> dict:
    """Build a minimal OTLP JSON payload."""
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service_name}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": scope},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def _span(
    name: str,
    trace_id: str = "abc123",
    span_id: str = "span001",
    parent_id: str = "",
    status_code: int = 1,
    start_ns: int = 1_000_000_000_000,
    end_ns: int = 1_000_100_000_000,
    attributes: list | None = None,
) -> dict:
    d = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": {"code": status_code},
        "attributes": attributes or [],
    }
    if parent_id:
        d["parentSpanId"] = parent_id
    return d


# ---------------------------------------------------------------------------
# Ingestion tests
# ---------------------------------------------------------------------------


class TestIngestion:
    def test_ingest_basic(self):
        payload = _otlp_payload(
            [
                _span("root", span_id="s1"),
                _span("child1", span_id="s2", parent_id="s1"),
                _span("child2", span_id="s3", parent_id="s1"),
            ]
        )
        r = client.post("/api/otel/v1/traces", json=payload)
        assert r.status_code == 200
        assert r.json()["spans_received"] == 3

    def test_sponsio_spans_flagged(self):
        payload = _otlp_payload(
            [_span("sponsio.agent_turn", span_id="s1")],
            scope="sponsio",
        )
        client.post("/api/otel/v1/traces", json=payload)
        flat = trace_store.get_trace_flat("abc123")
        assert flat is not None
        assert flat[0]["is_sponsio"] is True

    def test_non_sponsio_spans_not_flagged(self):
        payload = _otlp_payload([_span("langchain.agent", span_id="s1")])
        client.post("/api/otel/v1/traces", json=payload)
        flat = trace_store.get_trace_flat("abc123")
        assert flat[0]["is_sponsio"] is False

    def test_mixed_scopes(self):
        payload = {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "scope": {"name": "langchain"},
                            "spans": [_span("langchain.agent", span_id="s1")],
                        },
                        {
                            "scope": {"name": "sponsio"},
                            "spans": [
                                _span(
                                    "sponsio.agent_turn", span_id="s2", parent_id="s1"
                                )
                            ],
                        },
                    ],
                }
            ]
        }
        r = client.post("/api/otel/v1/traces", json=payload)
        assert r.json()["spans_received"] == 2
        flat = trace_store.get_trace_flat("abc123")
        scopes = {s["scope"] for s in flat}
        assert scopes == {"langchain", "sponsio"}

    def test_empty_payload(self):
        r = client.post("/api/otel/v1/traces", json={"resourceSpans": []})
        assert r.status_code == 200
        assert r.json()["spans_received"] == 0

    def test_accumulate_spans(self):
        payload1 = _otlp_payload([_span("span_a", span_id="s1")])
        payload2 = _otlp_payload([_span("span_b", span_id="s2", parent_id="s1")])
        client.post("/api/otel/v1/traces", json=payload1)
        client.post("/api/otel/v1/traces", json=payload2)
        flat = trace_store.get_trace_flat("abc123")
        assert len(flat) == 2


# ---------------------------------------------------------------------------
# Query tests
# ---------------------------------------------------------------------------


class TestQueries:
    def test_list_traces(self):
        payload = _otlp_payload([_span("root", span_id="s1")])
        client.post("/api/otel/v1/traces", json=payload)
        r = client.get("/api/otel/traces")
        assert r.status_code == 200
        traces = r.json()
        assert len(traces) == 1
        assert traces[0]["trace_id"] == "abc123"
        assert traces[0]["span_count"] == 1

    def test_list_traces_filter_violations(self):
        # Trace with violation
        payload1 = _otlp_payload(
            [
                _span("sponsio.agent_turn", trace_id="t1", span_id="s1", status_code=2),
                _span(
                    "sponsio.contract_check",
                    trace_id="t1",
                    span_id="s2",
                    parent_id="s1",
                    status_code=2,
                ),
            ],
            scope="sponsio",
        )
        # Trace without violation
        payload2 = _otlp_payload(
            [
                _span("langchain.agent", trace_id="t2", span_id="s3"),
            ]
        )
        client.post("/api/otel/v1/traces", json=payload1)
        client.post("/api/otel/v1/traces", json=payload2)

        all_traces = client.get("/api/otel/traces").json()
        assert len(all_traces) == 2

        violated = client.get("/api/otel/traces?has_violations=true").json()
        assert len(violated) == 1
        assert violated[0]["trace_id"] == "t1"

    def test_get_trace_tree(self):
        payload = _otlp_payload(
            [
                _span("root", span_id="s1"),
                _span("child", span_id="s2", parent_id="s1"),
            ]
        )
        client.post("/api/otel/v1/traces", json=payload)
        r = client.get("/api/otel/traces/abc123")
        assert r.status_code == 200
        tree = r.json()
        assert tree["trace_id"] == "abc123"
        assert len(tree["root_spans"]) == 1
        assert tree["root_spans"][0]["name"] == "root"
        assert len(tree["root_spans"][0]["children"]) == 1
        assert tree["root_spans"][0]["children"][0]["name"] == "child"

    def test_get_trace_flat(self):
        payload = _otlp_payload(
            [
                _span("second", span_id="s2", start_ns=2_000_000_000),
                _span("first", span_id="s1", start_ns=1_000_000_000),
            ]
        )
        client.post("/api/otel/v1/traces", json=payload)
        r = client.get("/api/otel/traces/abc123/flat")
        assert r.status_code == 200
        spans = r.json()["spans"]
        assert spans[0]["name"] == "first"
        assert spans[1]["name"] == "second"

    def test_get_nonexistent_trace(self):
        r = client.get("/api/otel/traces/nonexistent")
        assert r.status_code == 404

    def test_delete_traces(self):
        payload = _otlp_payload([_span("root", span_id="s1")])
        client.post("/api/otel/v1/traces", json=payload)
        assert trace_store.trace_count == 1
        r = client.delete("/api/otel/traces")
        assert r.status_code == 200
        assert trace_store.trace_count == 0


# ---------------------------------------------------------------------------
# Attribute parsing tests
# ---------------------------------------------------------------------------


class TestAttributeParsing:
    def test_string_value(self):
        payload = _otlp_payload(
            [
                _span(
                    "test",
                    span_id="s1",
                    attributes=[
                        {"key": "my.str", "value": {"stringValue": "hello"}},
                    ],
                )
            ]
        )
        client.post("/api/otel/v1/traces", json=payload)
        flat = trace_store.get_trace_flat("abc123")
        assert flat[0]["attributes"]["my.str"] == "hello"

    def test_bool_value(self):
        payload = _otlp_payload(
            [
                _span(
                    "test",
                    span_id="s1",
                    attributes=[
                        {"key": "my.bool", "value": {"boolValue": True}},
                    ],
                )
            ]
        )
        client.post("/api/otel/v1/traces", json=payload)
        flat = trace_store.get_trace_flat("abc123")
        assert flat[0]["attributes"]["my.bool"] is True

    def test_int_value(self):
        payload = _otlp_payload(
            [
                _span(
                    "test",
                    span_id="s1",
                    attributes=[
                        {"key": "my.int", "value": {"intValue": "42"}},
                    ],
                )
            ]
        )
        client.post("/api/otel/v1/traces", json=payload)
        flat = trace_store.get_trace_flat("abc123")
        assert flat[0]["attributes"]["my.int"] == 42

    def test_double_value(self):
        payload = _otlp_payload(
            [
                _span(
                    "test",
                    span_id="s1",
                    attributes=[
                        {"key": "my.double", "value": {"doubleValue": 3.14}},
                    ],
                )
            ]
        )
        client.post("/api/otel/v1/traces", json=payload)
        flat = trace_store.get_trace_flat("abc123")
        assert flat[0]["attributes"]["my.double"] == 3.14

    def test_missing_attributes(self):
        payload = _otlp_payload([_span("test", span_id="s1")])
        client.post("/api/otel/v1/traces", json=payload)
        flat = trace_store.get_trace_flat("abc123")
        assert flat[0]["attributes"] == {}


# ---------------------------------------------------------------------------
# Bridge test: push-span → trace store
# ---------------------------------------------------------------------------


class TestBridge:
    def test_push_span_appears_in_traces(self):
        sponsio_span = {
            "span_type": "sponsio.agent_turn",
            "start_time": 100.0,
            "end_time": 100.05,
            "status": "violated",
            "agent_id": "bot",
            "action": "issue_refund",
            "blocked": True,
            "det_violations": 1,
            "sto_violations": 0,
            "total_contracts_checked": 1,
            "children": [
                {
                    "span_type": "sponsio.contract_check",
                    "start_time": 100.01,
                    "end_time": 100.04,
                    "status": "violated",
                    "contract_name": "check_policy must precede issue_refund",
                    "pipeline": "det",
                    "children": [],
                }
            ],
        }
        r = client.post("/api/monitor/push-span", json=sponsio_span)
        assert r.status_code == 200

        traces = client.get("/api/otel/traces").json()
        assert len(traces) >= 1
        # Find the trace with sponsio spans
        sponsio_trace = next((t for t in traces if t["has_sponsio_spans"]), None)
        assert sponsio_trace is not None
        assert sponsio_trace["has_violations"] is True
        assert sponsio_trace["contracts_checked"] == 1
