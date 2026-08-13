"""Regression tests for list-endpoint pagination hardening (A10).

Verifies that the new `limit` / `offset` query parameters on the market router
reject illegal values (negative, zero, over-cap, non-numeric) via FastAPI
validation, and that valid values are bound as *parameterized* integers
(``LIMIT %s OFFSET %s``) rather than interpolated into the SQL string.
"""

import pathlib
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

from app.auth import Actor, get_actor  # noqa: E402
from app.routers.market import router as market_router  # noqa: E402
import app.routers.market as market  # noqa: E402


def _client_and_capture(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params=(), workspace_id=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(market, "fetch_all", fake_fetch_all)

    app = FastAPI()
    app.include_router(market_router)

    def fake_actor():
        return Actor("u1", "u@example.com", "User", False, None, "ws-1", "owner")

    app.dependency_overrides[get_actor] = fake_actor
    return TestClient(app), captured


def test_limit_rejects_negative_zero_and_over_max(monkeypatch):
    client, _ = _client_and_capture(monkeypatch)
    assert client.get("/api/orders?limit=-1").status_code == 422
    assert client.get("/api/orders?limit=0").status_code == 422
    assert client.get("/api/orders?limit=201").status_code == 422
    assert client.get("/api/orders?limit=abc").status_code == 422


def test_offset_rejects_negative_and_non_numeric(monkeypatch):
    client, _ = _client_and_capture(monkeypatch)
    assert client.get("/api/orders?offset=-1").status_code == 422
    assert client.get("/api/orders?offset=abc").status_code == 422


def test_valid_limit_offset_bound_as_parameterized_ints(monkeypatch):
    client, captured = _client_and_capture(monkeypatch)
    resp = client.get("/api/orders?limit=50&offset=10")
    assert resp.status_code == 200
    assert "LIMIT %s OFFSET %s" in captured["sql"]
    # bound as tuple params (psycopg2 %s placeholders), never f-string concatenation
    assert captured["params"] == ("ws-1", 50, 10)
    assert all(isinstance(p, int) for p in captured["params"][1:])


def test_defaults_clamp_to_page_limit_default(monkeypatch):
    client, captured = _client_and_capture(monkeypatch)
    assert client.get("/api/orders").status_code == 200
    # PAGE_LIMIT_DEFAULT=100, offset defaults to 0
    assert captured["params"] == ("ws-1", 100, 0)
