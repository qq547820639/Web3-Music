from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DB_PATH = os.getenv("SQLITE_PATH", "/data/payment/payment.db")
WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "dev-payment-webhook-secret")
# `processing`/`requires_action` intents never resolve themselves; without this
# timeout the order side stays in `payment_pending` forever. The order-side
# reconciliation treats a `payment_intent.failed` webhook as the canonical
# "give up and release" signal (see market.py payment_webhook), so we emit that.
PAYMENT_TIMEOUT_SECONDS = int(os.getenv("PAYMENT_TIMEOUT_SECONDS", "60"))
app = FastAPI(title="Payment Provider Emulator", version="1.0.0")


def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
      CREATE TABLE IF NOT EXISTS intents(
        id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,request_hash TEXT,amount INTEGER,currency TEXT,status TEXT,scenario TEXT,
        callback_url TEXT,metadata TEXT,created_at TEXT,updated_at TEXT
      )
    """)
    conn.execute("""
      CREATE TABLE IF NOT EXISTS refunds(
        id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,request_hash TEXT,intent_id TEXT,amount INTEGER,status TEXT,created_at TEXT
      )
    """)
    for table, column in (("intents", "request_hash"), ("refunds", "idempotency_key"), ("refunds", "request_hash")):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    conn.commit()
    return conn


class IntentCreate(BaseModel):
    amount: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    scenario: str = "success"
    callback_url: str
    metadata: dict = Field(default_factory=dict)


class RefundCreate(BaseModel):
    intent_id: str
    amount: int = Field(gt=0)
    callback_url: str
    metadata: dict = Field(default_factory=dict)


def _sign(raw: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _post(callback_url: str, event: dict, delay: float = 0.8):
    def run():
        time.sleep(delay)
        raw = json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
        try:
            httpx.post(callback_url, content=raw, headers={"Content-Type": "application/json", "X-Payment-Signature": _sign(raw)}, timeout=10)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


@app.get("/health")
def health():
    with db() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/v1/intents")
def create_intent(body: IntentCreate, idempotency_key: str = Header(alias="Idempotency-Key")):
    if body.scenario not in {"success", "failed", "requires_action", "processing"}:
        raise HTTPException(400, "unsupported scenario")
    now = datetime.now(timezone.utc).isoformat()
    request_hash = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with db() as conn:
        existing = conn.execute("SELECT * FROM intents WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            if existing["request_hash"] and existing["request_hash"] != request_hash:
                raise HTTPException(409, "idempotency key reused with different intent")
            return dict(existing)
        intent_id = "pi_" + uuid.uuid4().hex
        initial = "requires_action" if body.scenario == "requires_action" else "processing"
        conn.execute(
            "INSERT INTO intents(id,idempotency_key,request_hash,amount,currency,status,scenario,callback_url,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (intent_id, idempotency_key, request_hash, body.amount, body.currency.upper(), initial, body.scenario, body.callback_url, json.dumps(body.metadata), now, now),
        )
        conn.commit()
    if body.scenario in {"success", "failed"}:
        final = "succeeded" if body.scenario == "success" else "failed"
        with db() as conn:
            conn.execute("UPDATE intents SET status=?,updated_at=? WHERE id=?", (final, datetime.now(timezone.utc).isoformat(), intent_id))
            conn.commit()
        _post(body.callback_url, {
            "id": "evt_" + uuid.uuid4().hex,
            "type": f"payment_intent.{final}",
            "created": now,
            "data": {"object": {"id": intent_id, "amount": body.amount, "currency": body.currency.upper(), "status": final, "metadata": body.metadata}},
        })
    else:
        # processing / requires_action: emulate an async gateway that never
        # confirms. After PAYMENT_TIMEOUT_SECONDS resolve the intent as failed
        # and notify the order side so it can release the order/reservations.
        def _timeout_failed():
            time.sleep(PAYMENT_TIMEOUT_SECONDS)
            final = "failed"
            with db() as conn:
                conn.execute(
                    "UPDATE intents SET status=?,updated_at=? WHERE id=? AND status IN ('processing','requires_action')",
                    (final, datetime.now(timezone.utc).isoformat(), intent_id),
                )
                conn.commit()
            _post(body.callback_url, {
                "id": "evt_" + uuid.uuid4().hex,
                "type": "payment_intent.failed",
                "created": now,
                "data": {"object": {"id": intent_id, "amount": body.amount, "currency": body.currency.upper(), "status": final, "metadata": body.metadata}},
            })
        threading.Thread(target=_timeout_failed, daemon=True).start()
    return {"id": intent_id, "status": initial, "amount": body.amount, "currency": body.currency.upper(), "metadata": body.metadata}


@app.get("/v1/intents/{intent_id}")
def get_intent(intent_id: str):
    with db() as conn:
        row = conn.execute("SELECT * FROM intents WHERE id=?", (intent_id,)).fetchone()
    if not row:
        raise HTTPException(404, "intent not found")
    return dict(row)


@app.post("/v1/refunds")
def create_refund(body: RefundCreate, idempotency_key: str = Header(alias="Idempotency-Key")):
    now = datetime.now(timezone.utc).isoformat()
    request_hash = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with db() as conn:
        intent = conn.execute("SELECT * FROM intents WHERE id=?", (body.intent_id,)).fetchone()
        if not intent or intent["status"] != "succeeded":
            raise HTTPException(409, "payment is not refundable")
        refund_id = "re_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
        existing = conn.execute("SELECT * FROM refunds WHERE idempotency_key=? OR id=?", (idempotency_key, refund_id)).fetchone()
        if existing:
            if existing["request_hash"] and existing["request_hash"] != request_hash:
                raise HTTPException(409, "idempotency key reused with different refund")
            return dict(existing)
        refunded = conn.execute("SELECT COALESCE(sum(amount),0) total FROM refunds WHERE intent_id=? AND status='succeeded'", (body.intent_id,)).fetchone()["total"]
        if int(refunded) + body.amount > int(intent["amount"]):
            raise HTTPException(409, "refund exceeds payment amount")
        conn.execute("INSERT INTO refunds(id,idempotency_key,request_hash,intent_id,amount,status,created_at) VALUES(?,?,?,?,?,?,?)", (refund_id, idempotency_key, request_hash, body.intent_id, body.amount, "succeeded", now))
        conn.commit()
    _post(body.callback_url, {
        "id": "evt_" + uuid.uuid4().hex,
        "type": "refund.succeeded",
        "created": now,
        "data": {"object": {"id": refund_id, "payment_intent": body.intent_id, "amount": body.amount, "status": "succeeded", "metadata": body.metadata}},
    })
    return {"id": refund_id, "status": "succeeded", "amount": body.amount, "payment_intent": body.intent_id}
