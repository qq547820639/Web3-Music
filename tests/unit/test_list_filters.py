"""Regression tests for list-endpoint search/status/total hardening (B4).

Confirms that the new `q` (ILIKE search) and `status` filters are always bound
as psycopg2 ``%s`` parameters (never interpolated into the SQL string, even for
values containing quotes / wildcards), and that every list query exposes a
``COUNT(*) OVER()`` window so pagination can render page counts. ``/orders``
aliases that window ``row_total`` rather than ``total`` because ``o.*`` already
carries the orders.total money column.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

from app.auth import Actor  # noqa: E402
import app.main as main  # noqa: E402
import app.routers.assets as assets  # noqa: E402
import app.routers.market as market  # noqa: E402


def _actor():
    return Actor("u1", "u@example.com", "User", False, None, "ws-1", "owner")


def _patch_fetch_all(monkeypatch, module):
    captured = {}

    def fake_fetch_all(sql, params=(), workspace_id=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"total": 5, "id": "r1"}]

    monkeypatch.setattr(module, "fetch_all", fake_fetch_all)
    return captured


def test_list_projects_q_status_total_parameterized(monkeypatch):
    captured = _patch_fetch_all(monkeypatch, main)
    result = main.list_projects(limit=50, offset=10, q="O'Brien 100%", status="active", actor=_actor())

    assert "ILIKE %s" in captured["sql"]
    assert "O'Brien" not in captured["sql"]  # never string-interpolated
    assert any(p == "%O'Brien 100%%" for p in captured["params"])
    assert "active" in captured["params"]
    assert "COUNT(*) OVER()::int AS total" in captured["sql"]
    assert result == {"items": [{"total": 5, "id": "r1"}], "total": 5, "limit": 50, "offset": 10}


def test_list_assets_q_status_total_parameterized(monkeypatch):
    captured = _patch_fetch_all(monkeypatch, assets)
    result = assets.list_assets(limit=20, offset=0, q="drop%;--", status="verified", actor=_actor())

    assert "ILIKE %s" in captured["sql"]
    assert "drop%;--" not in captured["sql"]
    assert captured["params"].count("%drop%;--%") == 2  # title + id placeholders
    assert "verified" in captured["params"]
    assert "COUNT(*) OVER()::int AS total" in captured["sql"]
    assert result["total"] == 5


def test_list_orders_q_status_total_parameterized(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params=(), workspace_id=None):
        captured["sql"] = sql
        captured["params"] = params
        # orders.total is a real money column, so the window count needs its own alias.
        return [{"id": "o1", "total": 1200, "row_total": 5}, {"id": "o2", "total": 300, "row_total": 5}]

    monkeypatch.setattr(market, "fetch_all", fake_fetch_all)
    result = market.list_orders(limit=100, offset=0, q="refund' OR 1=1", status="paid", actor=_actor())

    assert "ILIKE %s" in captured["sql"]
    assert "refund' OR 1=1" not in captured["sql"]
    assert captured["params"].count("%refund' OR 1=1%") == 2
    assert "paid" in captured["params"]
    assert "COUNT(*) OVER()::int AS row_total" in captured["sql"]
    assert "AS total" not in captured["sql"], "window count must not alias onto the orders.total money column"
    assert result["total"] == 5
    assert [item["total"] for item in result["items"]] == [1200, 300], "listed order amounts were overwritten"
    assert all("row_total" not in item for item in result["items"]), "internal count alias leaked into the response"


def test_empty_result_total_falls_back_to_zero(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params=(), workspace_id=None):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    result = main.list_projects(limit=20, offset=0, q=None, status=None, actor=_actor())

    assert result["total"] == 0
    assert result["items"] == []
