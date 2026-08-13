from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg2.extras
from fastapi import Query

from .auth import Actor
from .db import fetch_one

# Unified list pagination defaults. Every list endpoint applies these caps so a
# single request can never pull an unbounded table into memory.
PAGE_LIMIT_DEFAULT = 100
PAGE_LIMIT_MAX = 200


def serialize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    if isinstance(value, (uuid.UUID, datetime, Decimal)):
        return str(value)
    return value


def setting_enabled(key: str) -> bool:
    row = fetch_one("SELECT value FROM system_settings WHERE key=%s", (key,))
    return bool(row and row["value"] is True)


def audit(cur, actor: Actor | None, action: str, subject_type: str, subject_id: str | None = None, payload: dict | None = None, request_id: str | None = None) -> None:
    cur.execute(
        "INSERT INTO audit_events(workspace_id,actor_id,actor_role,action,subject_type,subject_id,request_id,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            actor.workspace_id if actor else None,
            actor.user_id if actor else "system",
            actor.role if actor else None,
            action,
            subject_type,
            subject_id,
            request_id,
            psycopg2.extras.Json(payload or {}),
        ),
    )
