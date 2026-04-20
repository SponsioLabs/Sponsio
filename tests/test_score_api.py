"""Tests for the /api/score endpoint."""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    """Point the DB at a temp directory so tests don't touch real data."""
    import api.db as db_mod

    monkeypatch.setattr("api.db._DB_DIR", str(tmp_path))
    monkeypatch.setattr("api.db._DB_PATH", str(tmp_path / "scores.db"))
    monkeypatch.setattr("api.db._initialized", False)
    # Clear thread-local connection so it reconnects to the temp DB
    if hasattr(db_mod._local, "conn"):
        del db_mod._local.conn


@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/score
# ---------------------------------------------------------------------------


class TestCreateScore:
    def test_basic(self, client):
        resp = client.post(
            "/api/score",
            json={
                "agent_name": "test_bot",
                "tools": [
                    {"name": "list_items", "description": "List all items"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["score"] == 100
        assert data["grade"] == "A+"
        assert data["agent_name"] == "test_bot"
        assert data["id"] >= 1
        assert "badge_url" in data
        assert "img.shields.io" in data["badge_url"]

    def test_dangerous_tools(self, client):
        resp = client.post(
            "/api/score",
            json={
                "tools": [
                    {"name": "delete_user", "description": "Delete user from database"},
                    {"name": "send_email", "description": "Send email to recipient"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["score"] < 100
        assert data["grade"] != "A+"
        assert len(data["deductions"]) > 0
        assert len(data["suggested_contracts"]) > 0

    def test_empty_tools_rejected(self, client):
        resp = client.post("/api/score", json={"tools": []})
        assert resp.status_code == 422

    def test_default_agent_name(self, client):
        resp = client.post(
            "/api/score",
            json={
                "tools": [{"name": "get_item", "description": "Get item"}],
            },
        )
        assert resp.json()["agent_name"] == "anonymous"

    def test_deduction_structure(self, client):
        resp = client.post(
            "/api/score",
            json={
                "tools": [
                    {"name": "send_email", "description": "Send email via webhook"},
                ],
            },
        )
        data = resp.json()
        for d in data["deductions"]:
            assert "check_id" in d
            assert "points_lost" in d
            assert "description" in d
            assert "affected_tools" in d
            assert "suggested_contract" in d


# ---------------------------------------------------------------------------
# GET /api/score/{id}
# ---------------------------------------------------------------------------


class TestGetScore:
    def test_get_existing(self, client):
        create_resp = client.post(
            "/api/score",
            json={
                "agent_name": "bot_a",
                "tools": [{"name": "read_data", "description": "Read data"}],
            },
        )
        row_id = create_resp.json()["id"]

        resp = client.get(f"/api/score/{row_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == row_id
        assert resp.json()["agent_name"] == "bot_a"

    def test_get_nonexistent(self, client):
        resp = client.get("/api/score/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/score (list)
# ---------------------------------------------------------------------------


class TestListScores:
    def test_empty(self, client):
        resp = client.get("/api/score")
        assert resp.status_code == 200
        assert resp.json()["items"] == []
        assert resp.json()["count"] == 0

    def test_list_after_inserts(self, client):
        for i in range(3):
            client.post(
                "/api/score",
                json={
                    "agent_name": f"bot_{i}",
                    "tools": [{"name": "get_x", "description": "Get x"}],
                },
            )
        resp = client.get("/api/score")
        data = resp.json()
        assert data["count"] == 3
        # Newest first
        assert data["items"][0]["agent_name"] == "bot_2"

    def test_pagination(self, client):
        for i in range(5):
            client.post(
                "/api/score",
                json={
                    "agent_name": f"bot_{i}",
                    "tools": [{"name": "get_x", "description": "Get x"}],
                },
            )
        resp = client.get("/api/score?limit=2&offset=0")
        assert len(resp.json()["items"]) == 2
        assert resp.json()["count"] == 5  # total, not page size

        resp2 = client.get("/api/score?limit=2&offset=2")
        assert len(resp2.json()["items"]) == 2
        assert resp2.json()["count"] == 5
