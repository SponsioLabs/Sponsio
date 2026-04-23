"""Tests for monitor trace import."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from api.main import app
    from api.state import state

    state.reset()
    yield TestClient(app)
    state.reset()


def test_import_trace_replaces_monitor_trace(client):
    resp = client.post(
        "/api/monitor/import",
        json={
            "metadata": {"source": "unit-test"},
            "events": [
                {
                    "ts": 0,
                    "agent": "agent",
                    "type": "tool_call",
                    "tool": "lookup_order",
                }
            ],
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "imported", "event_count": 1}

    trace = client.get("/api/monitor/trace").json()
    assert trace["events"][0]["agent"] == "agent"
    assert trace["events"][0]["tool"] == "lookup_order"


def test_import_trace_accepts_otlp_payload(client):
    resp = client.post(
        "/api/monitor/import",
        json={
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "otel_agent"},
                            }
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "langgraph"},
                            "spans": [
                                {
                                    "traceId": "trace1",
                                    "spanId": "span1",
                                    "name": "check_refund_policy",
                                    "startTimeUnixNano": "1000",
                                    "endTimeUnixNano": "2000",
                                    "attributes": [
                                        {
                                            "key": "tool.input.order_id",
                                            "value": {"stringValue": "ord_123"},
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "imported", "event_count": 1}

    trace = client.get("/api/monitor/trace").json()
    assert trace["events"][0]["agent"] == "otel_agent"
    assert trace["events"][0]["tool"] == "check_refund_policy"
