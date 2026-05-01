"""Tests for the local single-user dashboard backend (`sponsio serve`).

Covers all read-only HTTP endpoints, the WebSocket live tail, and the
path-traversal guards. Uses ``fastapi.testclient.TestClient`` against
temp dirs — never touches ``~/.sponsio``.
"""

from __future__ import annotations

import json
import time

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


# ---------------------------------------------------------------------------
# /api/contracts — pattern catalog + sponsio.yaml.
# ---------------------------------------------------------------------------


def test_contracts_lists_pattern_catalog(client):
    body = client.get("/api/contracts").json()
    names = {p["name"] for p in body["patterns"]}
    # A few well-known det patterns must show up — if these disappear the
    # OSS contract surface has shrunk and the docs are out of sync.
    assert {"must_precede", "rate_limit", "idempotent", "no_data_leak"} <= names
    sample = next(p for p in body["patterns"] if p["name"] == "must_precede")
    assert sample["kind"] == "det"
    assert "before" in sample["params"] and "after" in sample["params"]
    # ``desc`` is boilerplate; it should be filtered out.
    assert "desc" not in sample["params"]


def test_contracts_yaml_is_none_when_no_config(tmp_path, monkeypatch):
    # Run from a CWD with no sponsio.yaml, no SPONSIO_CONFIG override.
    monkeypatch.delenv("SPONSIO_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app(sessions_dir=tmp_path / "sessions"))
    body = client.get("/api/contracts").json()
    assert body["yaml"] is None


def test_contracts_yaml_loads_via_env_override(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    cfg = tmp_path / "my-sponsio.yaml"
    cfg.write_text(
        "contracts:\n"
        "  - name: refund_needs_check\n"
        "    pattern: must_precede\n"
        "    args: {before: check_policy, after: issue_refund}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SPONSIO_CONFIG", str(cfg))
    client = TestClient(create_app(sessions_dir=tmp_path / "sessions"))
    body = client.get("/api/contracts").json()
    assert body["yaml"]["path"] == str(cfg)
    assert body["yaml"]["contracts"][0]["name"] == "refund_needs_check"


# ---------------------------------------------------------------------------
# /api/host/buckets — `~/.sponsio/plugins/<bucket>/`.
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    cursor = plugins / "_host_cursor"
    cursor.mkdir(parents=True)
    (cursor / "conv-aaa.shield-trace.jsonl").write_text(
        json.dumps({"ts": 10.0, "action": "edit"})
        + "\n"
        + json.dumps({"ts": 11.0, "action": "save"})
        + "\n",
        encoding="utf-8",
    )
    (cursor / "conv-bbb.shield-trace.jsonl").write_text(
        json.dumps({"ts": 12.0, "action": "run"}) + "\n",
        encoding="utf-8",
    )
    (cursor / "sponsio.yaml").write_text("contracts: []\n", encoding="utf-8")

    # An empty plugin dir — should still appear, with conv_count=0.
    (plugins / "filesystem").mkdir()
    return plugins


def test_host_buckets_lists_directories(tmp_path, populated_plugins):
    client = TestClient(
        create_app(sessions_dir=tmp_path / "sessions", plugins_dir=populated_plugins)
    )
    body = client.get("/api/host/buckets").json()
    by_name = {b["name"]: b for b in body["buckets"]}
    assert by_name["_host_cursor"]["conv_count"] == 2
    assert by_name["_host_cursor"]["has_yaml"] is True
    assert by_name["filesystem"]["conv_count"] == 0
    assert by_name["filesystem"]["has_yaml"] is False


def test_host_bucket_events_returns_sorted_tail(tmp_path, populated_plugins):
    client = TestClient(
        create_app(sessions_dir=tmp_path / "sessions", plugins_dir=populated_plugins)
    )
    body = client.get("/api/host/buckets/_host_cursor/events").json()
    timestamps = [e["ts"] for e in body["events"]]
    assert timestamps == sorted(timestamps)
    assert {e["action"] for e in body["events"]} == {"edit", "save", "run"}


def test_host_bucket_events_respects_limit(tmp_path, populated_plugins):
    client = TestClient(
        create_app(sessions_dir=tmp_path / "sessions", plugins_dir=populated_plugins)
    )
    body = client.get("/api/host/buckets/_host_cursor/events?limit=2").json()
    assert len(body["events"]) == 2
    # Tail = newest two, so ts=11.0 and ts=12.0.
    assert [e["ts"] for e in body["events"]] == [11.0, 12.0]


def test_host_bucket_events_unknown_bucket_404s(tmp_path, populated_plugins):
    client = TestClient(
        create_app(sessions_dir=tmp_path / "sessions", plugins_dir=populated_plugins)
    )
    r = client.get("/api/host/buckets/no_such_bucket/events")
    assert r.status_code == 404


def test_host_buckets_handles_missing_root(tmp_path):
    client = TestClient(
        create_app(sessions_dir=tmp_path / "sessions", plugins_dir=tmp_path / "absent")
    )
    body = client.get("/api/host/buckets").json()
    assert body["buckets"] == []


# ---------------------------------------------------------------------------
# Capabilities — flags must reflect the new endpoints being live.
# ---------------------------------------------------------------------------


def test_capabilities_reflects_live_endpoints(client):
    body = client.get("/api/capabilities").json()["features"]
    assert body["contract_browser"] is True
    assert body["host_buckets"] is True
    assert body["live_trace"] is True


# ---------------------------------------------------------------------------
# WS /api/live — incremental session-log tail.
# ---------------------------------------------------------------------------


def test_live_streams_new_events(populated_sessions):
    # Use a small poll interval so the test doesn't hang.
    app = create_app(sessions_dir=populated_sessions, poll_interval=0.05)
    client = TestClient(app)
    with client.websocket_connect("/api/live") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["sessions_dir"] == str(populated_sessions)

        # Append a new event; the tail loop should pick it up next tick.
        new_path = populated_sessions / "agent_a" / "20260501_120000_111.jsonl"
        with new_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": 99.0, "action": "tool.live"}) + "\n")
            f.flush()

        # Drain up to a few frames waiting for ours.
        deadline = time.monotonic() + 5.0
        seen_actions: list[str] = []
        while time.monotonic() < deadline:
            frame = ws.receive_json()
            if frame.get("type") == "event":
                seen_actions.append(frame["data"].get("action"))
                if "tool.live" in seen_actions:
                    break
        assert "tool.live" in seen_actions


def test_live_ignores_partial_trailing_line(populated_sessions):
    # A line written without a newline must NOT be emitted until newline
    # arrives — otherwise live tail emits half-records.
    app = create_app(sessions_dir=populated_sessions, poll_interval=0.05)
    client = TestClient(app)
    with client.websocket_connect("/api/live") as ws:
        assert ws.receive_json()["type"] == "ready"

        target = populated_sessions / "agent_a" / "20260501_120000_111.jsonl"
        with target.open("a", encoding="utf-8") as f:
            f.write('{"ts": 100.0, "action": "tool.partial"')  # no closing brace, no \n
            f.flush()

        # Wait long enough for several poll cycles; nothing should fire.
        time.sleep(0.4)

        # Now complete the line.
        with target.open("a", encoding="utf-8") as f:
            f.write("}\n")
            f.flush()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            frame = ws.receive_json()
            if (
                frame.get("type") == "event"
                and frame["data"].get("action") == "tool.partial"
            ):
                return
        pytest.fail("partial-then-complete event was never emitted")
