"""Tests for scan dashboard endpoints."""

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


def _insert_scan(use_case: str = "scan_upload") -> int:
    from api import db

    return db.insert_score(
        agent_name="scan_bot",
        score=100,
        grade="A+",
        timestamp="2026-04-22T00:00:00Z",
        details={
            "deductions": [],
            "suggested_contracts": [],
            "yaml_content": "tools: []\n",
            "source_filename": "sponsio.yaml",
        },
        description="Uploaded: sponsio.yaml",
        use_case=use_case,
        is_public=False,
    )


def test_invalid_history_source_is_rejected(client):
    _insert_scan()

    resp = client.get("/api/scan/history?source=typo")

    assert resp.status_code == 422


def test_invalid_delete_source_does_not_delete_upload_scans(client):
    scan_id = _insert_scan()

    resp = client.delete("/api/scan/history?source=typo")

    assert resp.status_code == 422
    assert client.get(f"/api/scan/{scan_id}").status_code == 200
