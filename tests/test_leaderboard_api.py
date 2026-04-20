"""Tests for the /api/leaderboard endpoint."""

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
    if hasattr(db_mod._local, "conn"):
        del db_mod._local.conn


@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


def _submit(
    client,
    name,
    tools,
    *,
    display_name=None,
    is_public=False,
    framework=None,
    description=None,
):
    """Helper to POST a score submission."""
    body = {
        "agent_name": name,
        "tools": tools,
        "is_public": is_public,
    }
    if display_name is not None:
        body["display_name"] = display_name
    if framework is not None:
        body["framework"] = framework
    if description is not None:
        body["description"] = description
    return client.post("/api/score", json=body)


_SAFE_TOOLS = [{"name": "list_items", "description": "List all items"}]
_RISKY_TOOLS = [
    {"name": "delete_user", "description": "Delete user from database"},
    {"name": "send_email", "description": "Send email to recipient"},
]


# ---------------------------------------------------------------------------
# GET /api/leaderboard
# ---------------------------------------------------------------------------


class TestLeaderboard:
    def test_empty(self, client):
        resp = client.get("/api/leaderboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["count"] == 0

    def test_private_not_shown(self, client):
        _submit(client, "bot_a", _SAFE_TOOLS, is_public=False)
        resp = client.get("/api/leaderboard")
        assert resp.json()["count"] == 0

    def test_public_without_display_name_not_shown(self, client):
        _submit(client, "bot_a", _SAFE_TOOLS, is_public=True)
        resp = client.get("/api/leaderboard")
        assert resp.json()["count"] == 0

    def test_public_with_display_name_shown(self, client):
        _submit(client, "bot_a", _SAFE_TOOLS, display_name="SafeBot", is_public=True)
        resp = client.get("/api/leaderboard")
        data = resp.json()
        assert data["count"] == 1
        assert data["entries"][0]["display_name"] == "SafeBot"
        assert data["entries"][0]["rank"] == 1

    def test_ranked_by_score_desc(self, client):
        # Safe bot scores higher than risky bot
        _submit(client, "safe", _SAFE_TOOLS, display_name="SafeBot", is_public=True)
        _submit(client, "risky", _RISKY_TOOLS, display_name="RiskyBot", is_public=True)
        resp = client.get("/api/leaderboard")
        entries = resp.json()["entries"]
        assert len(entries) == 2
        assert entries[0]["display_name"] == "SafeBot"
        assert entries[0]["rank"] == 1
        assert entries[1]["display_name"] == "RiskyBot"
        assert entries[1]["rank"] == 2
        assert entries[0]["score"] >= entries[1]["score"]

    def test_best_score_per_display_name(self, client):
        """Same display_name submitted twice — only best score shown."""
        _submit(client, "bot", _RISKY_TOOLS, display_name="MyBot", is_public=True)
        _submit(client, "bot", _SAFE_TOOLS, display_name="MyBot", is_public=True)
        resp = client.get("/api/leaderboard")
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["score"] == 100  # the safe submission

    def test_framework_filter(self, client):
        _submit(
            client,
            "a",
            _SAFE_TOOLS,
            display_name="LG Bot",
            is_public=True,
            framework="langgraph",
        )
        _submit(
            client,
            "b",
            _SAFE_TOOLS,
            display_name="CR Bot",
            is_public=True,
            framework="crewai",
        )
        resp = client.get("/api/leaderboard?framework=langgraph")
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["display_name"] == "LG Bot"

    def test_badge_url_in_entry(self, client):
        _submit(client, "bot", _SAFE_TOOLS, display_name="SafeBot", is_public=True)
        entries = client.get("/api/leaderboard").json()["entries"]
        assert "img.shields.io" in entries[0]["badge_url"]

    def test_email_not_exposed(self, client):
        """Email should never appear in leaderboard responses."""
        _submit(client, "bot", _SAFE_TOOLS, display_name="Bot", is_public=True)
        client.post(
            "/api/score",
            json={
                "agent_name": "secret_bot",
                "tools": _SAFE_TOOLS,
                "display_name": "SecretBot",
                "email": "secret@example.com",
                "is_public": True,
            },
        ).json()
        entries = client.get("/api/leaderboard").json()["entries"]
        for e in entries:
            assert "email" not in e

    def test_description_shown(self, client):
        _submit(
            client,
            "bot",
            _SAFE_TOOLS,
            display_name="MyBot",
            is_public=True,
            description="A fintech customer service bot",
        )
        entries = client.get("/api/leaderboard").json()["entries"]
        assert entries[0]["description"] == "A fintech customer service bot"


# ---------------------------------------------------------------------------
# GET /api/leaderboard/stats
# ---------------------------------------------------------------------------


class TestLeaderboardStats:
    def test_empty(self, client):
        resp = client.get("/api/leaderboard/stats")
        data = resp.json()
        assert data["total_submissions"] == 0
        assert data["public_entries"] == 0
        assert data["average_score"] == 0.0
        assert data["top_agent"] is None

    def test_counts(self, client):
        _submit(client, "a", _SAFE_TOOLS, display_name="Bot A", is_public=True)
        _submit(client, "b", _RISKY_TOOLS, is_public=False)
        _submit(
            client,
            "c",
            _SAFE_TOOLS,
            display_name="Bot C",
            is_public=True,
            framework="langgraph",
        )

        data = client.get("/api/leaderboard/stats").json()
        assert data["total_submissions"] == 3
        assert data["public_entries"] == 2
        assert data["average_score"] > 0

    def test_grade_distribution(self, client):
        _submit(client, "a", _SAFE_TOOLS, display_name="A", is_public=True)
        _submit(client, "b", _SAFE_TOOLS, display_name="B", is_public=True)
        data = client.get("/api/leaderboard/stats").json()
        assert "A+" in data["grade_distribution"]
        assert data["grade_distribution"]["A+"] == 2

    def test_framework_distribution(self, client):
        _submit(
            client,
            "a",
            _SAFE_TOOLS,
            display_name="A",
            is_public=True,
            framework="langgraph",
        )
        _submit(
            client,
            "b",
            _SAFE_TOOLS,
            display_name="B",
            is_public=True,
            framework="langgraph",
        )
        _submit(
            client,
            "c",
            _SAFE_TOOLS,
            display_name="C",
            is_public=True,
            framework="crewai",
        )
        data = client.get("/api/leaderboard/stats").json()
        assert data["framework_distribution"]["langgraph"] == 2
        assert data["framework_distribution"]["crewai"] == 1

    def test_top_agent(self, client):
        _submit(client, "risky", _RISKY_TOOLS, display_name="Risky", is_public=True)
        _submit(client, "safe", _SAFE_TOOLS, display_name="Safe", is_public=True)
        data = client.get("/api/leaderboard/stats").json()
        assert data["top_agent"]["display_name"] == "Safe"
        assert data["top_agent"]["score"] == 100

    def test_top_agent_excludes_private(self, client):
        _submit(client, "private_safe", _SAFE_TOOLS, is_public=False)
        _submit(
            client, "public_risky", _RISKY_TOOLS, display_name="Public", is_public=True
        )
        data = client.get("/api/leaderboard/stats").json()
        assert data["top_agent"]["display_name"] == "Public"


# ---------------------------------------------------------------------------
# Period filtering
# ---------------------------------------------------------------------------


class TestPeriodFiltering:
    def test_all_returns_everything(self, client):
        _submit(client, "a", _SAFE_TOOLS, display_name="A", is_public=True)
        resp = client.get("/api/leaderboard?period=all")
        assert resp.json()["count"] == 1

    def test_today_returns_recent(self, client):
        """Submissions just made should appear in 'today' filter."""
        _submit(client, "a", _SAFE_TOOLS, display_name="A", is_public=True)
        resp = client.get("/api/leaderboard?period=today")
        assert resp.json()["count"] == 1

    def test_week_returns_recent(self, client):
        _submit(client, "a", _SAFE_TOOLS, display_name="A", is_public=True)
        resp = client.get("/api/leaderboard?period=week")
        assert resp.json()["count"] == 1


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestLeaderboardPagination:
    def test_limit_offset(self, client):
        for i in range(5):
            _submit(
                client, f"bot_{i}", _SAFE_TOOLS, display_name=f"Bot {i}", is_public=True
            )
        resp = client.get("/api/leaderboard?limit=2&offset=0")
        assert len(resp.json()["entries"]) == 2
        assert resp.json()["count"] == 5  # total, not page size
        assert resp.json()["entries"][0]["rank"] == 1

        resp2 = client.get("/api/leaderboard?limit=2&offset=2")
        assert len(resp2.json()["entries"]) == 2
        assert resp2.json()["count"] == 5
        assert resp2.json()["entries"][0]["rank"] == 3
