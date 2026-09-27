from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg2.extras
from fastapi import HTTPException, Query

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

def _db_refusal(exc: psycopg2.Error, default: int = 409) -> HTTPException:
    """Turn a refusal from the database into the answer the caller can act on.

    The codes are chosen by the functions themselves: 23505 for 'this has already settled', 42501 for
    'you are not the role that may', P0002 for 'that row does not exist', 22023 for a bad enumeration --
    and 23514 comes from the table, when a body that the request model let through fails a CHECK.
    Each maps to a client-visible decision; anything else is a fault and stays a fault -- flattening an
    unexpected error into a 409 would hide a bug behind a refusal, which is the opposite of what the
    brand-award door did before this existed (it let a RAISE reach the browser as a 500).
    """
    code = getattr(exc, "pgcode", None)
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else "refused"
    if code == "22023":
        return HTTPException(422, message)
    if code == "P0002":
        return HTTPException(404, message)
    if code == "42501":
        return HTTPException(403, message)
    if code == "23514":
        # A CHECK on the row the caller asked for: the body is the thing at fault, so this answers with
        # the same 422 the endpoint's own pre-checks give rather than with a conflict. Only the
        # constraint's name is quoted back -- psycopg2's message also names the table, and the
        # constraint names are the vocabulary 021 chose for itself (report_email_shape, ...).
        name = getattr(getattr(exc, "diag", None), "constraint_name", None)
        if name:
            return HTTPException(422, {"code": "rejected_by_record_shape", "constraint": name})
        return HTTPException(422, message)
    return HTTPException(default, message)
