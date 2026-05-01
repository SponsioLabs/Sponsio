"""Tests for the local single-user dashboard backend (`sponsio serve`).

Covers the four shipped read-only endpoints and the path-traversal
guard. Uses ``fastapi.testclient.TestClient`` against a temp
sessions dir — never touches ``~/.sponsio``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from sponsio.serve import create_app  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _write_trace(sessions_dir, agent_id: str, fname: str, events: list[dict]) -> None:
    agent_dir = sessions_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / fname
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


@pytest.fixture
def populated_sessions(tmp_path):
    """A sessions tree with two agents and a couple of trace files."""
    sessions = tmp_path / "sessions"
    _write_trace(
        sessions,
        "agent_a",
        "20260501_120000_111.jsonl",
        [
            {"ts": 1.0, "agent_id": "agent_a", "action": "tool.x", "pipeline": "det"},
            {"ts": 2.0, "agent_id": "agent_a", "action": "tool.y", "pipeline": "det"},
        ],
    )
    _write_trace(
        sessions,
        "agent_a",
        "20260501_130000_222.jsonl",
        [{"ts": 3.0, "agent_id": "agent_a", "action": "tool.z", "pipeline": "det"}],
    )
    _write_trace(
        sessions,
        "agent_b",
        "20260501_140000_333.jsonl",
        [{"ts": 4.0, "agent_id": "agent_b", "action": "tool.q", "pipeline": "sto"}],
    )
    return sessions


@pytest.fixture
def client(populated_sessions):
    return TestClient(create_app(sessions_dir=populated_sessions))


# ---------------------------------------------------------------------------
# /api/capabilities — frontend feature flags.
# ---------------------------------------------------------------------------


def test_capabilities_reports_oss_tier(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "oss"
    assert body["features"]["session_browser"] is True
    assert body["features"]["trace_viewer"] is True
    # Cloud-only features must always be off in OSS.
    assert body["features"]["sto_judge"] is False
    assert body["features"]["multi_tenant"] is False
    assert body["features"]["hosted_ingestion"] is False


# ---------------------------------------------------------------------------
# /api/sessions — agent listing.
# ---------------------------------------------------------------------------


def test_list_sessions_returns_agents_with_counts(client):
    r = client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    by_id = {a["agent_id"]: a for a in body["agents"]}
    assert by_id["agent_a"]["trace_count"] == 2
    assert by_id["agent_b"]["trace_count"] == 1
    # Latest mtime is set; concrete value depends on the FS.
    assert by_id["agent_a"]["latest_mtime"] > 0


def test_list_sessions_skips_empty_dirs(tmp_path):
    sessions = tmp_path / "sessions"
    (sessions / "empty_agent").mkdir(parents=True)
    client = TestClient(create_app(sessions_dir=sessions))
    body = client.get("/api/sessions").json()
    assert body["agents"] == []


def test_list_sessions_handles_missing_root(tmp_path):
    # No sessions dir at all — should not 500.
    client = TestClient(create_app(sessions_dir=tmp_path / "nope"))
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json()["agents"] == []


# ---------------------------------------------------------------------------
# /api/sessions/{agent_id}/traces — trace listing.
# ---------------------------------------------------------------------------


def test_list_traces_for_agent(client):
    r = client.get("/api/sessions/agent_a/traces")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_id"] == "agent_a"
    ids = {t["trace_id"] for t in body["traces"]}
    assert ids == {"20260501_120000_111", "20260501_130000_222"}


def test_list_traces_unknown_agent_404s(client):
    r = client.get("/api/sessions/no_such_agent/traces")
    assert r.status_code == 404


def test_list_traces_rejects_path_traversal(client):
    # FastAPI normalises ``..`` in the path before dispatch, so a literal
    # traversal returns 404 from the router. The safe_join_segment guard
    # is the second line of defence — exercised below in trace_events.
    r = client.get("/api/sessions/..%2Fetc/traces")
    assert r.status_code in (400, 404)


# ---------------------------------------------------------------------------
# /api/sessions/{agent_id}/traces/{trace_id} — trace events.
# ---------------------------------------------------------------------------


def test_read_trace_events(client):
    r = client.get("/api/sessions/agent_a/traces/20260501_120000_111")
    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == "20260501_120000_111"
    assert len(body["events"]) == 2
    assert body["events"][0]["action"] == "tool.x"


def test_read_trace_unknown_id_404s(client):
    r = client.get("/api/sessions/agent_a/traces/does_not_exist")
    assert r.status_code == 404


def test_read_trace_drops_malformed_lines(tmp_path):
    sessions = tmp_path / "sessions"
    agent_dir = sessions / "agent_x"
    agent_dir.mkdir(parents=True)
    path = agent_dir / "t.jsonl"
    path.write_text(
        '{"ts": 1.0, "action": "good"}\n'
        "this is not json\n"
        '{"ts": 2.0, "action": "also_good"}\n',
        encoding="utf-8",
    )
    client = TestClient(create_app(sessions_dir=sessions))
    body = client.get("/api/sessions/agent_x/traces/t").json()
    assert [e["action"] for e in body["events"]] == ["good", "also_good"]


def test_read_trace_rejects_traversal_in_trace_id(client):
    # ``..`` segments survive into the trace_id path param even after
    # FastAPI's normalisation, so this is the real test of the guard.
    r = client.get("/api/sessions/agent_a/traces/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)
