"""End-to-end checks for the dashboard playground journey."""

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


def test_blocked_playground_action_does_not_poison_next_action(client):
    client.post(
        "/api/contracts",
        json={
            "agent_id": "journey_agent",
            "nl_text": "tool `check_refund_policy` must precede `issue_refund`",
        },
    )

    blocked = client.post(
        "/api/playground/action",
        json={"agent_id": "journey_agent", "action": "issue_refund"},
    )
    assert blocked.status_code == 200
    assert blocked.json()["allowed"] is False

    policy_check = client.post(
        "/api/playground/action",
        json={"agent_id": "journey_agent", "action": "check_refund_policy"},
    )
    assert policy_check.status_code == 200
    assert policy_check.json()["allowed"] is True

    refund = client.post(
        "/api/playground/action",
        json={"agent_id": "journey_agent", "action": "issue_refund"},
    )
    assert refund.status_code == 200
    assert refund.json()["allowed"] is True

    trace = client.get("/api/monitor/trace").json()
    assert [e["tool"] for e in trace["events"]] == [
        "check_refund_policy",
        "issue_refund",
    ]
