"""Regression test for the jsonb typecaster registration in app/db.py.

The API layer reads jsonb columns (revision.spec, locked_paths, quality
dimensions/variables/risks, rights manifests, snapshots, generation step
errors, etc.). Without `register_default_jsonb`, psycopg2 returns those
columns as JSON *strings* instead of dicts, which silently breaks downstream
dict/list consumers (e.g. `domain.patches.apply_patch`) and forces the
frontend to defensively `JSON.parse` every field.

This test verifies that every connection handed out by `db.connection()`
has `psycopg2.extras.register_default_jsonb(conn, loads=json.loads)` applied.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

import app.db as db  # noqa: E402


class _FakeSemaphore:
    def acquire(self, timeout=None):
        return True

    def release(self):
        pass


class _FakeConn:
    closed = False

    def rollback(self):
        pass


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()

    def getconn(self):
        return self.conn

    def putconn(self, conn, close=False):
        pass


def test_connection_registers_default_jsonb(monkeypatch):
    registered = {}

    def fake_register(conn, loads=None):
        registered["conn"] = conn
        registered["loads"] = loads

    fake_pool = _FakePool()
    monkeypatch.setattr(db, "_pool", fake_pool)
    monkeypatch.setattr(db, "_pool_slots", _FakeSemaphore())
    monkeypatch.setattr(db.psycopg2.extras, "register_default_jsonb", fake_register)

    with db.connection() as conn:
        assert conn is fake_pool.conn

    assert registered["conn"] is fake_pool.conn
    assert registered["loads"] is json.loads
