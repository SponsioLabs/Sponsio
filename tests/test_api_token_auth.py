"""Tests for the optional ``X-Sponsio-Token`` middleware.

Behaviors verified:

* No env var → no auth required (legacy/dev workflow).
* Env var set → /api/* requires the header; /api/health and static
  paths bypass; OPTIONS preflight bypasses; wrong / missing token
  yields 401.
* Constant-time comparison: not directly observable in tests, but we
  verify both correct and prefix-of-correct tokens are rejected as
  expected (i.e. no early-return short-circuit on partial match).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


class TestNoTokenConfigured:
    def test_health_open(self, client, monkeypatch):
        monkeypatch.delenv("SPONSIO_API_TOKEN", raising=False)
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_api_endpoint_open_without_token_env(self, client, monkeypatch):
        monkeypatch.delenv("SPONSIO_API_TOKEN", raising=False)
        # Any read endpoint — should respond normally.
        r = client.get("/api/system")
        assert r.status_code == 200


class TestTokenConfigured:
    def test_missing_token_yields_401(self, client, monkeypatch):
        monkeypatch.setenv("SPONSIO_API_TOKEN", "s3cr3t")
        r = client.get("/api/system")
        assert r.status_code == 401
        assert "X-Sponsio-Token" in r.json()["detail"]

    def test_wrong_token_yields_401(self, client, monkeypatch):
        monkeypatch.setenv("SPONSIO_API_TOKEN", "s3cr3t")
        r = client.get("/api/system", headers={"X-Sponsio-Token": "wrong"})
        assert r.status_code == 401

    def test_prefix_token_yields_401(self, client, monkeypatch):
        # Sanity: comparison isn't a startswith check.
        monkeypatch.setenv("SPONSIO_API_TOKEN", "s3cr3t-long-token")
        r = client.get("/api/system", headers={"X-Sponsio-Token": "s3cr3t-long"})
        assert r.status_code == 401

    def test_correct_token_passes(self, client, monkeypatch):
        monkeypatch.setenv("SPONSIO_API_TOKEN", "s3cr3t")
        r = client.get("/api/system", headers={"X-Sponsio-Token": "s3cr3t"})
        assert r.status_code == 200

    def test_health_bypasses(self, client, monkeypatch):
        monkeypatch.setenv("SPONSIO_API_TOKEN", "s3cr3t")
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_static_bypasses(self, client, monkeypatch):
        monkeypatch.setenv("SPONSIO_API_TOKEN", "s3cr3t")
        # Any /static path — even nonexistent — must skip the auth
        # middleware and reach the StaticFiles handler (which then
        # 404s, not 401).
        r = client.get("/static/does-not-exist.css")
        assert r.status_code != 401

    def test_options_preflight_bypasses(self, client, monkeypatch):
        monkeypatch.setenv("SPONSIO_API_TOKEN", "s3cr3t")
        r = client.options(
            "/api/system",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Should not be 401 — preflight goes through CORS middleware.
        assert r.status_code != 401

    def test_blank_env_var_treated_as_unset(self, client, monkeypatch):
        monkeypatch.setenv("SPONSIO_API_TOKEN", "   ")
        # Whitespace-only token must NOT silently enable auth (and
        # then accept any header value).
        r = client.get("/api/system")
        assert r.status_code == 200
