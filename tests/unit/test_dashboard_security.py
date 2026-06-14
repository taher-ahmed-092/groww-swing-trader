"""Dashboard security: token gating, localhost-only internal, sanitize, no-raise."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config.settings import settings
from dashboard.server import app, collect_dashboard_data, sanitize

_TOKEN = "test-secret-token-123"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_secret_token", _TOKEN)
    return TestClient(app)


def test_invalid_token_blocked(client):
    assert client.get("/dashboard/wrongtoken").status_code == 401


def test_valid_token_allowed(client):
    r = client.get(f"/dashboard/{_TOKEN}")
    assert r.status_code == 200
    assert "Cosmic Punk" in r.text


def test_internal_event_rejects_external(client):
    # TestClient's client host is not localhost → must be rejected.
    r = client.post("/internal/event", json={"hi": 1})
    assert r.status_code == 403


def test_collect_data_never_raises():
    data = collect_dashboard_data()
    assert isinstance(data, dict)
    assert "timestamp" in data


def test_sanitize_removes_keys():
    out = sanitize({"api_key": "sk-secret", "total": 5, "auth_token": "x"})
    assert out["api_key"] == "[REDACTED]"
    assert out["auth_token"] == "[REDACTED]"
    assert out["total"] == 5
