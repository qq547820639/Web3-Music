from __future__ import annotations

import hashlib
import hmac
import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import Actor, require_roles
from ..common import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX, audit, serialize, setting_enabled
from ..db import fetch_all, fetch_one, transaction
from ..domain.commerce import assert_offer_rights, canonical_hash, issue_credits, order_number, revoke_credits
from ..domain.events import emit
from ..settings import settings
from ..storage import client as s3_client

router = APIRouter(prefix="/api", tags=["Market OS"])


class CreditOrderCreate(BaseModel):
    sku: str
    quantity: int = Field(default=1, ge=1, le=20)


class PaymentStart(BaseModel):
    scenario: str = Field(default="success", pattern="^(success|failed|requires_action|processing)$")


class OfferCreate(BaseModel):
    asset_snapshot_id: str
    license_template_id: str = "12121212-1212-1212-1212-121212121212"
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    price_amount: int = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    territory: str = "worldwide"
    duration_days: int | None = Field(default=365, ge=1, le=36500)
    exclusive: bool = False
    status: str = Field(default="draft", pattern="^(draft|active)$")


class LicensePurchase(BaseModel):
    licensee_name: str = Field(min_length=1, max_length=200)


class RefundCreate(BaseModel):
    amount: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=3, max_length=1000)


class BriefCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=10000)
    budget_amount: int = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    deadline: str | None = None
    requirements: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="draft", pattern="^(draft|open)$")


class SubmissionCreate(BaseModel):
    asset_snapshot_id: str
    notes: str = Field(default="", max_length=4000)


class SubmissionReview(BaseModel):
    status: str = Field(pattern="^(shortlisted|rejected)$")


class BrandAwardCreate(BaseModel):
    licensee_name: str = Field(min_length=1, max_length=200)
    territory: str = Field(default="worldwide", min_length=2, max_length=200)
    duration_days: int | None = Field(default=365, ge=1, le=36500)


class TicketCreate(BaseModel):
    category: str = Field(pattern="^(generation|billing|rights|account|marketplace|other)$")
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    related_type: str | None = None
    related_id: str | None = None


def _catalog(sku: str):
    row = fetch_one("SELECT * FROM product_catalog WHERE sku=%s AND active=true", (sku,))
    if not row:
        raise HTTPException(404, "catalog item not found")
    return row


def _order(order_id: str, actor: Actor, for_update: bool = False, cur=None):
    sql = "SELECT * FROM orders WHERE id=%s AND workspace_id=%s" + (" FOR UPDATE" if for_update else "")
    if cur:
        cur.execute(sql, (order_id, actor.workspace_id)); row = cur.fetchone()
    else:
        row = fetch_one(sql, (order_id, actor.workspace_id), actor.workspace_id)
    if not row:
        raise HTTPException(404, "order not found")
    return row


def _create_order(cur, actor: Actor, order_type: str, currency: str, subtotal: int, subject_type: str | None, subject_id: str | None, metadata: dict, items: list[dict], order_id: str | None = None):
    order_id = order_id or str(uuid.uuid4())
    number = order_number(order_id)
    cur.execute(
        "INSERT INTO orders(id,workspace_id,order_number,order_type,status,currency,subtotal,tax,total,customer_user_id,subject_type,subject_id,metadata) VALUES(%s,%s,%s,%s,'pending',%s,%s,0,%s,%s,%s,%s,%s) RETURNING *",
        (order_id, actor.workspace_id, number, order_type, currency.upper(), subtotal, subtotal, actor.user_id, subject_type, subject_id, psycopg2.extras.Json(metadata)),
    )
    order = cur.fetchone()
    for item in items:
        cur.execute(
            "INSERT INTO order_items(workspace_id,order_id,catalog_id,description,quantity,unit_amount,total,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (actor.workspace_id, order_id, item.get("catalog_id"), item["description"], item["quantity"], item["unit_amount"], item["total"], psycopg2.extras.Json(item.get("metadata", {}))),
        )
    emit(cur, "OrderCreated", "order", order_id, {"order_type": order_type, "total": subtotal, "currency": currency.upper()}, actor.workspace_id)
    return order


def _fulfill_paid_order(cur, workspace_id: str, order_id: str, payment_id: str):
    cur.execute("SELECT * FROM orders WHERE id=%s AND workspace_id=%s FOR UPDATE", (order_id, workspace_id))
    order = cur.fetchone()
    if not order:
        raise ValueError("order not found")
    if order["status"] == "fulfilled":
        return
    metadata = order["metadata"] or {}
    if order["order_type"] == "credit_purchase":
        credits = int(metadata["credits"])
        issue_credits(cur, workspace_id, credits, f"payment:{payment_id}:credits", payment_id, "payment-webhook")
    elif order["order_type"] == "subscription":
        now = datetime.now(timezone.utc)
        cur.execute(
            "INSERT INTO subscriptions(workspace_id,order_id,catalog_id,status,current_period_start,current_period_end,provider_subscription_id) VALUES(%s,%s,%s,'active',%s,%s,%s)",
            (workspace_id, order_id, metadata["catalog_id"], now, now + timedelta(days=30), f"sub_{payment_id}"),
        )
    elif order["order_type"] in {"license", "brand_task"}:
        license_id = str(uuid.uuid4())
        starts = datetime.now(timezone.utc)
        ends = starts + timedelta(days=int(metadata["duration_days"])) if metadata.get("duration_days") else None
        terms = metadata["terms_snapshot"]
        license_payload = {
            "license_id": license_id,
            "order_id": order_id,
            "seller_workspace_id": metadata["seller_workspace_id"],
            "buyer_workspace_id": workspace_id,
            "asset_snapshot_id": metadata["asset_snapshot_id"],
            "rights_manifest_id": metadata["rights_manifest_id"],
            "license_template_id": metadata["license_template_id"],
            "licensee_name": metadata["licensee_name"],
            "territory": metadata["territory"],
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat() if ends else None,
            "terms": terms,
        }
        cur.execute(
            "INSERT INTO licenses(id,seller_workspace_id,buyer_workspace_id,order_id,asset_snapshot_id,rights_manifest_id,license_template_id,status,licensee_name,territory,starts_at,ends_at,terms_snapshot,license_hash,activated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,now())",
            (license_id, metadata["seller_workspace_id"], workspace_id, order_id, metadata["asset_snapshot_id"], metadata["rights_manifest_id"], metadata["license_template_id"], metadata["licensee_name"], metadata["territory"], starts, ends, psycopg2.extras.Json(terms), canonical_hash(license_payload)),
        )
        cur.execute(
            "INSERT INTO deliveries(workspace_id,order_id,license_id,status,expires_at) VALUES(%s,%s,%s,'ready',%s)",
            (workspace_id, order_id, license_id, ends),
        )
        if order["order_type"] == "license":
            cur.execute("UPDATE offer_reservations SET status='confirmed',updated_at=now() WHERE order_id=%s AND buyer_workspace_id=%s", (order_id, workspace_id))
            cur.execute("UPDATE asset_offers SET status='sold',updated_at=now() WHERE id=%s AND exclusive=true", (metadata["offer_id"],))
        else:
            cur.execute("SELECT * FROM set_brand_submission_status(%s,'awarded')", (metadata["submission_id"],))
            cur.execute("UPDATE brand_briefs SET status='awarded',updated_at=now() WHERE id=%s AND workspace_id=%s", (metadata["brief_id"], workspace_id))
        cur.execute("SELECT record_license_revenue(%s)", (license_id,))
    cur.execute("UPDATE orders SET status='fulfilled',updated_at=now() WHERE id=%s", (order_id,))
    emit(cur, "OrderFulfilled", "order", order_id, {"payment_id": payment_id, "order_type": order["order_type"]}, workspace_id)


@router.get("/catalog")
def catalog(actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing"))):
    return serialize(fetch_all("SELECT * FROM product_catalog WHERE active=true ORDER BY product_type,unit_amount"))


@router.post("/orders/credits", status_code=201)
def create_credit_order(body: CreditOrderCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "billing"))):
    item = _catalog(body.sku)
    if item["product_type"] not in {"credit_pack", "subscription"}:
        raise HTTPException(400, "catalog item cannot be purchased here")
    subtotal = int(item["unit_amount"]) * body.quantity
    credits = int(item["credits"] or 0) * body.quantity
    order_type = "subscription" if item["product_type"] == "subscription" else "credit_purchase"
    metadata = {"sku": item["sku"], "credits": credits, "catalog_id": str(item["id"])}
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        order = _create_order(cur, actor, order_type, item["currency"], subtotal, "catalog", str(item["id"]), metadata, [{"catalog_id": item["id"], "description": item["name"], "quantity": body.quantity, "unit_amount": item["unit_amount"], "total": subtotal}])
        audit(cur, actor, "order.create", "order", str(order["id"]), {"sku": body.sku, "quantity": body.quantity}, request.state.request_id)
    return serialize(order)


@router.get("/orders")
def list_orders(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing"))):
    return serialize(fetch_all(
        "SELECT o.*,COALESCE(json_agg(i ORDER BY i.description) FILTER (WHERE i.id IS NOT NULL),'[]') items FROM orders o LEFT JOIN order_items i ON i.order_id=o.id WHERE o.workspace_id=%s GROUP BY o.id ORDER BY o.created_at DESC LIMIT %s OFFSET %s",
        (actor.workspace_id, limit, offset), actor.workspace_id,
    ))


@router.post("/orders/{order_id}/pay", status_code=202)
def pay_order(order_id: str, body: PaymentStart, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), actor: Actor = Depends(require_roles("owner", "admin", "billing"))):
    if not setting_enabled("payments_enabled"):
        raise HTTPException(503, "payments are disabled")
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        order = _order(order_id, actor, True, cur)
        request_hash = canonical_hash({"order_id": order_id, "scenario": body.scenario, "amount": int(order["total"]), "currency": order["currency"]})
        cur.execute("SELECT * FROM payments WHERE workspace_id=%s AND idempotency_key=%s", (actor.workspace_id, idempotency_key))
        existing = cur.fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "payment idempotency key was reused with a different request")
            if existing["provider_payment_id"] or existing["status"] not in {"created", "processing"}:
                return serialize(existing)
            payment_id = str(existing["id"])
            audit(cur, actor, "payment.provider_retry", "payment", payment_id, {"order_id": order_id}, request.state.request_id)
        else:
            if order["status"] not in {"pending", "payment_pending"}:
                raise HTTPException(409, f"order cannot be paid from status {order['status']}")
            payment_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO payments(id,workspace_id,order_id,provider,idempotency_key,request_hash,status,currency,amount) VALUES(%s,%s,%s,%s,%s,%s,'created',%s,%s)",
                (payment_id, actor.workspace_id, order_id, settings.payment_provider, idempotency_key, request_hash, order["currency"], order["total"]),
            )
            cur.execute("UPDATE orders SET status='payment_pending',updated_at=now() WHERE id=%s", (order_id,))
            audit(cur, actor, "payment.start", "payment", payment_id, {"order_id": order_id, "scenario": body.scenario}, request.state.request_id)
    payload = {
        "amount": int(order["total"]), "currency": order["currency"], "scenario": body.scenario,
        "callback_url": f"{settings.public_api_base_url}/api/payment-webhooks/{settings.payment_provider}",
        "metadata": {"payment_id": payment_id, "order_id": order_id, "workspace_id": actor.workspace_id},
    }
    try:
        response = httpx.post(f"{settings.payment_base_url}/v1/intents", json=payload, headers={"Idempotency-Key": idempotency_key}, timeout=15)
        response.raise_for_status()
        provider = response.json()
    except httpx.ConnectError as exc:
        with transaction(actor.workspace_id) as conn, conn.cursor() as cur:
            cur.execute("UPDATE payments SET status='failed',raw=%s,updated_at=now() WHERE id=%s", (psycopg2.extras.Json({"error": str(exc), "certainty": "not_connected"}), payment_id))
            cur.execute("UPDATE orders SET status='pending',updated_at=now() WHERE id=%s", (order_id,))
        raise HTTPException(502, f"payment provider unavailable: {exc}")
    except httpx.HTTPStatusError as exc:
        if 400 <= exc.response.status_code < 500:
            with transaction(actor.workspace_id) as conn, conn.cursor() as cur:
                cur.execute("UPDATE payments SET status='failed',raw=%s,updated_at=now() WHERE id=%s", (psycopg2.extras.Json({"error": str(exc), "status_code": exc.response.status_code}), payment_id))
                cur.execute("UPDATE orders SET status='pending',updated_at=now() WHERE id=%s", (order_id,))
            raise HTTPException(502, f"payment provider rejected request: {exc}")
        with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE payments SET status='processing',raw=%s,updated_at=now() WHERE id=%s RETURNING *", (psycopg2.extras.Json({"error": str(exc), "certainty": "unknown"}), payment_id))
            return serialize(cur.fetchone())
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE payments SET status='processing',raw=%s,updated_at=now() WHERE id=%s RETURNING *", (psycopg2.extras.Json({"error": str(exc), "certainty": "unknown", "retry_with_same_idempotency_key": True}), payment_id))
            return serialize(cur.fetchone())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE payments SET provider_payment_id=%s,status=CASE WHEN status IN ('succeeded','partially_refunded','refunded') THEN status ELSE %s END,raw=%s,updated_at=now() WHERE id=%s RETURNING *",
            (provider["id"], provider["status"], psycopg2.extras.Json(provider), payment_id),
        )
        row = cur.fetchone()
    return serialize(row)


@router.post("/payment-webhooks/{provider}")
async def payment_webhook(provider: str, request: Request, x_payment_signature: str | None = Header(default=None, alias="X-Payment-Signature")):
    if provider != settings.payment_provider:
        raise HTTPException(404, "unknown payment provider")
    raw = await request.body()
    expected = hmac.new(settings.payment_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    valid = bool(x_payment_signature and hmac.compare_digest(expected, x_payment_signature))
    if not valid:
        raise HTTPException(401, "invalid payment webhook signature")
    try:
        payload = json.loads(raw)
        event_id = payload["id"]; event_type = payload["type"]; obj = payload["data"]["object"]
        metadata = obj.get("metadata") or {}; workspace_id = metadata["workspace_id"]; payment_id = metadata["payment_id"]; order_id = metadata["order_id"]
    except Exception:
        raise HTTPException(400, "invalid payment webhook payload")
    with transaction(workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO payment_inbox(provider,event_id,event_type,signature_valid,payload) VALUES(%s,%s,%s,true,%s) "
            "ON CONFLICT(provider,event_id) DO NOTHING RETURNING id",
            (provider, event_id, event_type, psycopg2.extras.Json(payload)),
        )
        if not cur.fetchone():
            return {"accepted": True, "duplicate": True}
        cur.execute("SELECT * FROM payments WHERE id=%s AND workspace_id=%s FOR UPDATE", (payment_id, workspace_id))
        payment = cur.fetchone()
        if not payment:
            raise HTTPException(404, "payment not found")
        if payment["provider"] != provider or str(payment["order_id"]) != str(order_id):
            raise HTTPException(409, "payment webhook subject mismatch")
        if payment.get("provider_payment_id") and str(payment["provider_payment_id"]) != str(obj.get("id")) and event_type.startswith("payment_intent."):
            raise HTTPException(409, "provider payment id mismatch")
        if event_type == "payment_intent.succeeded":
            if int(obj.get("amount", -1)) != int(payment["amount"]) or str(obj.get("currency", "")).upper() != str(payment["currency"]).upper():
                raise HTTPException(409, "payment amount or currency mismatch")
            if payment["status"] not in {"succeeded", "partially_refunded", "refunded"}:
                cur.execute("UPDATE payments SET status='succeeded',raw=%s,updated_at=now() WHERE id=%s", (psycopg2.extras.Json(obj), payment_id))
                cur.execute("UPDATE orders SET status='paid',updated_at=now() WHERE id=%s", (order_id,))
                _fulfill_paid_order(cur, workspace_id, order_id, payment_id)
        elif event_type == "payment_intent.failed":
            if payment["status"] not in {"succeeded", "partially_refunded", "refunded"}:
                cur.execute("UPDATE payments SET status='failed',raw=%s,updated_at=now() WHERE id=%s", (psycopg2.extras.Json(obj), payment_id))
                cur.execute("UPDATE orders SET status='pending',updated_at=now() WHERE id=%s", (order_id,))
                cur.execute("UPDATE offer_reservations SET status='released',updated_at=now() WHERE order_id=%s AND status='pending'", (order_id,))
        elif event_type == "refund.succeeded":
            refund_id = metadata.get("refund_id")
            refund_amount = int(obj.get("amount", 0))
            if refund_amount <= 0 or refund_amount > int(payment["amount"] - payment["refunded_amount"]):
                raise HTTPException(409, "refund amount exceeds remaining payment")
            if not refund_id:
                raise HTTPException(400, "refund webhook is missing refund_id")
            cur.execute("SELECT status FROM refunds WHERE id=%s AND workspace_id=%s FOR UPDATE", (refund_id, workspace_id))
            refund = cur.fetchone()
            if not refund:
                raise HTTPException(404, "refund not found")
            if refund["status"] != "succeeded":
                cur.execute("UPDATE refunds SET status='succeeded',provider_refund_id=%s,updated_at=now() WHERE id=%s", (obj["id"], refund_id))
                cur.execute(
                    "UPDATE payments SET status=CASE WHEN refunded_amount+%s>=amount THEN 'refunded' ELSE 'partially_refunded' END,"
                    "refunded_amount=LEAST(amount,refunded_amount+%s),updated_at=now() WHERE id=%s RETURNING status",
                    (refund_amount, refund_amount, payment_id),
                )
                payment_status = cur.fetchone()["status"]
                if payment_status == "refunded":
                    cur.execute("UPDATE orders SET status='refunded',updated_at=now() WHERE id=%s", (order_id,))
                    cur.execute("UPDATE licenses SET status='refunded' WHERE order_id=%s", (order_id,))
                    cur.execute("UPDATE deliveries SET status='revoked' WHERE order_id=%s", (order_id,))
                    cur.execute("UPDATE subscriptions SET status='cancelled',cancel_at_period_end=false,updated_at=now() WHERE order_id=%s", (order_id,))
                    cur.execute("UPDATE offer_reservations SET status='released',updated_at=now() WHERE order_id=%s", (order_id,))
                    cur.execute("UPDATE asset_offers SET status='paused',updated_at=now() WHERE id=(SELECT subject_id::uuid FROM orders WHERE id=%s AND subject_type='asset_offer') AND exclusive=true", (order_id,))
                    cur.execute("SELECT reverse_license_revenue(%s)", (order_id,))
                    cur.execute("SELECT order_type,metadata FROM orders WHERE id=%s", (order_id,))
                    refunded_order = cur.fetchone()
                    if refunded_order and refunded_order["order_type"] == "brand_task":
                        metadata = refunded_order["metadata"] or {}
                        cur.execute("SELECT * FROM set_brand_submission_status(%s,'shortlisted')", (metadata.get("submission_id"),))
                        cur.execute("UPDATE brand_briefs SET status='review',updated_at=now() WHERE id=%s AND workspace_id=%s", (metadata.get("brief_id"), workspace_id))
        cur.execute("UPDATE payment_inbox SET processed_at=now() WHERE provider=%s AND event_id=%s", (provider, event_id))
    return {"accepted": True, "duplicate": False}


@router.post("/orders/{order_id}/refunds", status_code=202)
def refund_order(order_id: str, body: RefundCreate, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), actor: Actor = Depends(require_roles("owner", "admin", "billing"))):
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        order = _order(order_id, actor, True, cur)
        cur.execute("SELECT * FROM payments WHERE order_id=%s AND workspace_id=%s AND status IN ('succeeded','partially_refunded') ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (order_id, actor.workspace_id))
        payment = cur.fetchone()
        if not payment:
            raise HTTPException(409, "no refundable payment")
        amount = body.amount or int(payment["amount"] - payment["refunded_amount"])
        if amount <= 0 or amount > int(payment["amount"] - payment["refunded_amount"]):
            raise HTTPException(400, "invalid refund amount")
        request_hash = canonical_hash({"order_id": order_id, "payment_id": str(payment["id"]), "amount": amount, "reason": body.reason})
        cur.execute("SELECT * FROM refunds WHERE workspace_id=%s AND idempotency_key=%s", (actor.workspace_id, idempotency_key))
        existing = cur.fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "refund idempotency key was reused with a different request")
            if existing["status"] in {"succeeded", "failed"}:
                return serialize(existing)
            refund_id = str(existing["id"])
            row = existing
            credits_to_revoke = int((existing["metadata"] or {}).get("credits_revoked", 0))
        else:
            refund_id = str(uuid.uuid4())
            credits_to_revoke = 0
            if order["order_type"] == "credit_purchase":
                full_credits = int((order["metadata"] or {}).get("credits", 0))
                payment_amount = int(payment["amount"])
                before_refund = int(payment["refunded_amount"])
                after_refund = before_refund + amount
                before_credits = (full_credits * before_refund) // payment_amount if payment_amount else 0
                after_credits = full_credits if after_refund >= payment_amount else (full_credits * after_refund) // payment_amount
                credits_to_revoke = max(0, after_credits - before_credits)
                if credits_to_revoke:
                    try:
                        revoke_credits(cur, actor.workspace_id, credits_to_revoke, f"refund:{refund_id}:credits", refund_id, actor.user_id)
                    except ValueError as exc:
                        raise HTTPException(409, str(exc))
            refund_metadata = {"credits_revoked": credits_to_revoke}
            cur.execute(
                "INSERT INTO refunds(id,workspace_id,order_id,payment_id,idempotency_key,request_hash,amount,reason,metadata,status,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'processing',%s) RETURNING *",
                (refund_id, actor.workspace_id, order_id, payment["id"], idempotency_key, request_hash, amount, body.reason, psycopg2.extras.Json(refund_metadata), actor.user_id),
            )
            row = cur.fetchone()
            audit(cur, actor, "refund.start", "refund", refund_id, {"order_id": order_id, "amount": amount}, request.state.request_id)
    payload = {"intent_id": payment["provider_payment_id"], "amount": amount, "callback_url": f"{settings.public_api_base_url}/api/payment-webhooks/{settings.payment_provider}", "metadata": {"payment_id": str(payment["id"]), "order_id": order_id, "workspace_id": actor.workspace_id, "refund_id": refund_id}}
    provider_key = f"refund:{refund_id}"
    try:
        response = httpx.post(f"{settings.payment_base_url}/v1/refunds", json=payload, headers={"Idempotency-Key": provider_key}, timeout=15)
        response.raise_for_status()
    except httpx.ConnectError as exc:
        with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE refunds SET status='failed',metadata=metadata||%s,updated_at=now() WHERE id=%s", (psycopg2.extras.Json({"provider_error": str(exc), "certainty": "not_connected"}), refund_id))
            if order["order_type"] == "credit_purchase" and credits_to_revoke > 0:
                issue_credits(cur, actor.workspace_id, credits_to_revoke, f"refund:{refund_id}:compensate", refund_id, actor.user_id)
        raise HTTPException(502, f"refund provider unavailable: {exc}")
    except httpx.HTTPStatusError as exc:
        if 400 <= exc.response.status_code < 500:
            with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("UPDATE refunds SET status='failed',metadata=metadata||%s,updated_at=now() WHERE id=%s", (psycopg2.extras.Json({"provider_error": str(exc), "status_code": exc.response.status_code}), refund_id))
                if order["order_type"] == "credit_purchase" and credits_to_revoke > 0:
                    issue_credits(cur, actor.workspace_id, credits_to_revoke, f"refund:{refund_id}:compensate", refund_id, actor.user_id)
            raise HTTPException(502, f"refund provider rejected request: {exc}")
        with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE refunds SET metadata=metadata||%s,updated_at=now() WHERE id=%s RETURNING *", (psycopg2.extras.Json({"provider_error": str(exc), "certainty": "unknown", "retry_with_same_idempotency_key": True}), refund_id))
            return serialize(cur.fetchone())
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE refunds SET metadata=metadata||%s,updated_at=now() WHERE id=%s RETURNING *", (psycopg2.extras.Json({"provider_error": str(exc), "certainty": "unknown", "retry_with_same_idempotency_key": True}), refund_id))
            return serialize(cur.fetchone())
    return serialize(row)


@router.get("/marketplace/offers")
def marketplace_offers(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing"))):
    if not setting_enabled("marketplace_enabled"):
        raise HTTPException(503, "marketplace is disabled")
    return serialize(fetch_all("SELECT * FROM marketplace_offers_public ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset)))


@router.get("/offers")
def own_offers(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing"))):
    return serialize(fetch_all("SELECT * FROM asset_offers WHERE workspace_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s", (actor.workspace_id, limit, offset), actor.workspace_id))


@router.post("/offers", status_code=201)
def create_offer(body: OfferCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator"))):
    asset = fetch_one("SELECT * FROM asset_snapshots WHERE id=%s AND workspace_id=%s", (body.asset_snapshot_id, actor.workspace_id), actor.workspace_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    manifest = fetch_one("SELECT * FROM rights_manifests WHERE asset_snapshot_id=%s AND workspace_id=%s ORDER BY version DESC LIMIT 1", (body.asset_snapshot_id, actor.workspace_id), actor.workspace_id)
    if not manifest:
        raise HTTPException(409, "rights manifest required")
    template = fetch_one("SELECT * FROM license_templates WHERE id=%s AND status='active' AND (workspace_id IS NULL OR workspace_id=%s)", (body.license_template_id, actor.workspace_id))
    if not template:
        raise HTTPException(404, "license template not found")
    if body.status == "active":
        if not setting_enabled("licenses_enabled") or not setting_enabled("marketplace_enabled"):
            raise HTTPException(503, "licensing or marketplace is disabled")
        try:
            assert_offer_rights(manifest["manifest"])
        except ValueError as exc:
            raise HTTPException(409, str(exc))
    offer_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("INSERT INTO asset_offers(id,workspace_id,asset_snapshot_id,rights_manifest_id,license_template_id,title,description,price_amount,currency,territory,duration_days,exclusive,status,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *", (offer_id, actor.workspace_id, body.asset_snapshot_id, manifest["id"], body.license_template_id, body.title, body.description, body.price_amount, body.currency.upper(), body.territory, body.duration_days, body.exclusive, body.status, actor.user_id))
        row = cur.fetchone(); audit(cur, actor, "offer.create", "asset_offer", offer_id, {"asset_id": body.asset_snapshot_id, "status": body.status}, request.state.request_id)
    return serialize(row)


@router.post("/marketplace/offers/{offer_id}/purchase", status_code=201)
def purchase_offer(offer_id: str, body: LicensePurchase, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "billing"))):
    offer = fetch_one("SELECT * FROM marketplace_offers_public WHERE id=%s", (offer_id,))
    if not offer:
        raise HTTPException(404, "active offer not found")
    if str(offer["seller_workspace_id"]) == actor.workspace_id:
        raise HTTPException(409, "seller cannot purchase its own offer")
    try:
        assert_offer_rights(offer["rights_manifest"])
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    template = fetch_one("SELECT * FROM license_templates WHERE id=%s AND status='active'", (offer["license_template_id"],))
    if not template:
        raise HTTPException(409, "license template unavailable")
    metadata = {"offer_id": offer_id, "seller_workspace_id": str(offer["seller_workspace_id"]), "asset_snapshot_id": str(offer["asset_snapshot_id"]), "rights_manifest_id": str(offer["rights_manifest_id"]), "rights_version": offer["rights_version"], "license_template_id": str(offer["license_template_id"]), "terms_snapshot": template["terms"], "licensee_name": body.licensee_name, "territory": offer["territory"], "duration_days": offer["duration_days"], "exclusive": offer["exclusive"]}
    order_id = str(uuid.uuid4())
    reservation_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        order = _create_order(cur, actor, "license", offer["currency"], int(offer["price_amount"]), "asset_offer", offer_id, metadata, [{"description": f"License: {offer['title']}", "quantity": 1, "unit_amount": offer["price_amount"], "total": offer["price_amount"], "metadata": {"asset_snapshot_id": str(offer["asset_snapshot_id"])}}], order_id=order_id)
        try:
            cur.execute("SELECT reserve_marketplace_offer(%s,%s,%s)", (offer_id, order_id, reservation_id))
        except Exception as exc:
            raise HTTPException(409, f"offer is no longer purchasable: {exc}")
        audit(cur, actor, "license.order.create", "order", str(order["id"]), {"offer_id": offer_id, "reservation_id": reservation_id}, request.state.request_id)
    return serialize(order)


@router.get("/licenses")
def list_licenses(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal"))):
    return serialize(fetch_all("SELECT * FROM licenses WHERE seller_workspace_id=%s OR buyer_workspace_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s", (actor.workspace_id, actor.workspace_id, limit, offset), actor.workspace_id))


@router.get("/deliveries")
def list_deliveries(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal"))):
    return serialize(fetch_all("SELECT d.*,l.license_hash,l.asset_snapshot_id,l.licensee_name FROM deliveries d LEFT JOIN licenses l ON l.id=d.license_id WHERE d.workspace_id=%s ORDER BY d.created_at DESC LIMIT %s OFFSET %s", (actor.workspace_id, limit, offset), actor.workspace_id))


@router.get("/deliveries/{delivery_id}/export")
def export_delivery(delivery_id: str, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal"))):
    if not setting_enabled("exports_enabled"):
        raise HTTPException(503, "exports are disabled")
    row = fetch_one("SELECT * FROM licensed_delivery_package(%s)", (delivery_id,), actor.workspace_id)
    if not row:
        raise HTTPException(404, "delivery not found")
    if row["delivery_status"] not in {"ready", "downloaded"}:
        raise HTTPException(409, "delivery is not available")
    if row.get("expires_at") and row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(410, "delivery has expired")
    payload = row["package_payload"]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("license/license.json", json.dumps(payload["license"], ensure_ascii=False, indent=2, default=str))
        zf.writestr("license/rights-manifest.json", json.dumps(payload["rights_manifest"], ensure_ascii=False, indent=2, default=str))
        zf.writestr("asset/asset-snapshot.json", json.dumps(payload["asset_snapshot"], ensure_ascii=False, indent=2, default=str))
        zf.writestr("README.txt", "This package is governed by license/license.json and the bound rights manifest. It is not a copyright registration or legal opinion.\n")
        if row.get("bucket") and row.get("object_key"):
            obj = s3_client().get_object(Bucket=row["bucket"], Key=row["object_key"])
            audio = obj["Body"].read()
            extension = ".wav" if row.get("mime_type") == "audio/wav" else ".audio"
            zf.writestr("media/master" + extension, audio)
    data = archive.getvalue()
    package_hash = hashlib.sha256(data).hexdigest()
    with transaction(actor.workspace_id) as conn, conn.cursor() as cur:
        cur.execute("UPDATE deliveries SET status='downloaded',downloaded_at=COALESCE(downloaded_at,now()),package_hash=%s WHERE id=%s AND workspace_id=%s", (package_hash, delivery_id, actor.workspace_id))
        audit(cur, actor, "delivery.export", "delivery", delivery_id, {"package_hash": package_hash}, request.state.request_id)
    return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="delivery-{delivery_id}.zip"', "X-Package-SHA256": package_hash})


@router.get("/payouts")
def list_payouts(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(require_roles("owner", "admin", "billing"))):
    return serialize(fetch_all("SELECT * FROM payouts WHERE workspace_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s", (actor.workspace_id, limit, offset), actor.workspace_id))


@router.get("/brand-briefs/public")
def public_briefs(actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer"))):
    if not setting_enabled("brand_market_enabled"):
        raise HTTPException(503, "brand market is disabled")
    return serialize(fetch_all("SELECT * FROM brand_briefs_public ORDER BY created_at DESC"))


@router.get("/brand-briefs")
def own_briefs(actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing"))):
    if not setting_enabled("brand_market_enabled"):
        raise HTTPException(503, "brand market is disabled")
    return serialize(fetch_all("SELECT * FROM brand_briefs WHERE workspace_id=%s ORDER BY created_at DESC", (actor.workspace_id,), actor.workspace_id))


@router.post("/brand-briefs", status_code=201)
def create_brief(body: BriefCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator", "billing"))):
    if not setting_enabled("brand_market_enabled"):
        raise HTTPException(503, "brand market is disabled")
    brief_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("INSERT INTO brand_briefs(id,workspace_id,title,description,budget_amount,currency,deadline,requirements,status,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *", (brief_id, actor.workspace_id, body.title, body.description, body.budget_amount, body.currency.upper(), body.deadline, psycopg2.extras.Json(body.requirements), body.status, actor.user_id))
        row = cur.fetchone(); audit(cur, actor, "brand_brief.create", "brand_brief", brief_id, {"status": body.status}, request.state.request_id)
    return serialize(row)


@router.post("/brand-briefs/{brief_id}/submissions", status_code=201)
def submit_to_brief(brief_id: str, body: SubmissionCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator"))):
    if not setting_enabled("brand_market_enabled"):
        raise HTTPException(503, "brand market is disabled")
    brief = fetch_one("SELECT * FROM brand_briefs_public WHERE id=%s", (brief_id,))
    if not brief:
        raise HTTPException(404, "open brief not found")
    asset = fetch_one("SELECT * FROM asset_snapshots WHERE id=%s AND workspace_id=%s", (body.asset_snapshot_id, actor.workspace_id), actor.workspace_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    manifest = fetch_one("SELECT * FROM rights_manifests WHERE asset_snapshot_id=%s AND workspace_id=%s ORDER BY version DESC LIMIT 1", (body.asset_snapshot_id, actor.workspace_id), actor.workspace_id)
    if not manifest:
        raise HTTPException(409, "rights manifest required")
    try:
        assert_offer_rights(manifest["manifest"])
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    submission_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("INSERT INTO brand_submissions(id,brief_id,submitting_workspace_id,asset_snapshot_id,rights_manifest_id,notes,submitted_by) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *", (submission_id, brief_id, actor.workspace_id, body.asset_snapshot_id, manifest["id"], body.notes, actor.user_id))
        row = cur.fetchone(); audit(cur, actor, "brand_submission.create", "brand_submission", submission_id, {"brief_id": brief_id}, request.state.request_id)
    return serialize(row)


@router.post("/brand-submissions/{submission_id}/award", status_code=201)
def award_submission(submission_id: str, body: BrandAwardCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "billing"))):
    if not setting_enabled("brand_market_enabled") or not setting_enabled("payments_enabled"):
        raise HTTPException(503, "brand awards or payments are disabled")
    prepared = fetch_one("SELECT * FROM prepare_brand_award(%s)", (submission_id,), actor.workspace_id)
    if not prepared:
        raise HTTPException(404, "awardable submission not found")
    existing = fetch_one(
        "SELECT * FROM orders WHERE workspace_id=%s AND order_type='brand_task' AND subject_type='brand_submission' AND subject_id=%s AND status NOT IN ('cancelled','refunded') ORDER BY created_at DESC LIMIT 1",
        (actor.workspace_id, submission_id), actor.workspace_id,
    )
    if existing:
        return serialize(existing)
    template = fetch_one("SELECT * FROM license_templates WHERE id=%s AND status='active'", ("12121212-1212-1212-1212-121212121212",), actor.workspace_id)
    if not template:
        raise HTTPException(409, "active brand license template is unavailable")
    metadata = {
        "brief_id": str(prepared["brief_id"]), "submission_id": submission_id,
        "seller_workspace_id": str(prepared["seller_workspace_id"]),
        "asset_snapshot_id": str(prepared["asset_snapshot_id"]),
        "rights_manifest_id": str(prepared["rights_manifest_id"]),
        "license_template_id": str(template["id"]), "licensee_name": body.licensee_name,
        "territory": body.territory, "duration_days": body.duration_days,
        "terms_snapshot": {**template["terms"], "brand_brief_id": str(prepared["brief_id"]), "brand_submission_id": submission_id},
    }
    try:
        with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            order = _create_order(
                cur, actor, "brand_task", prepared["currency"], int(prepared["budget_amount"]),
                "brand_submission", submission_id, metadata,
                [{"description": f"Brand award: {prepared['brief_title']}", "quantity": 1, "unit_amount": int(prepared["budget_amount"]), "total": int(prepared["budget_amount"]), "metadata": {"brief_id": str(prepared["brief_id"]), "submission_id": submission_id}}],
            )
            cur.execute("SELECT * FROM set_brand_submission_status(%s,'shortlisted')", (submission_id,))
            cur.execute("UPDATE brand_briefs SET status='review',updated_at=now() WHERE id=%s AND workspace_id=%s", (prepared["brief_id"], actor.workspace_id))
            audit(cur, actor, "brand_submission.award_order", "order", str(order["id"]), {"brief_id": str(prepared["brief_id"]), "submission_id": submission_id}, request.state.request_id)
    except psycopg2.IntegrityError:
        # The partial unique index is the final concurrency guard. A racing request
        # returns the already-created active award instead of surfacing a 500.
        existing = fetch_one(
            "SELECT * FROM orders WHERE workspace_id=%s AND order_type='brand_task' AND subject_type='brand_submission' AND subject_id=%s AND status NOT IN ('cancelled','refunded') ORDER BY created_at DESC LIMIT 1",
            (actor.workspace_id, submission_id), actor.workspace_id,
        )
        if existing:
            return serialize(existing)
        raise HTTPException(409, "brand submission award conflicted; retry with the same submission")
    return serialize(order)


@router.get("/brand-briefs/{brief_id}/submissions")
def review_submissions(brief_id: str, limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(require_roles("owner", "admin", "reviewer"))):
    try:
        return serialize(fetch_all("SELECT * FROM review_brand_submissions(%s) LIMIT %s OFFSET %s", (brief_id, limit, offset), actor.workspace_id))
    except psycopg2.Error as exc:
        # Only the DB function's business guard maps to 403. Any real database
        # failure must surface as a 5xx so it is retried instead of being
        # misreported as an authorization problem.
        if "brief owner required" in str(exc):
            raise HTTPException(403, "brief owner required")
        raise


@router.put("/brand-submissions/{submission_id}")
def update_submission(submission_id: str, body: SubmissionReview, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "reviewer"))):
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute("SELECT * FROM set_brand_submission_status(%s,%s)", (submission_id, body.status)); row = cur.fetchone()
        except psycopg2.Error as exc:
            if "brief owner required" in str(exc):
                raise HTTPException(403, "brief owner required")
            raise
        audit(cur, actor, "brand_submission.review", "brand_submission", submission_id, {"status": body.status}, request.state.request_id)
    return serialize(row)


@router.get("/support/tickets")
def list_tickets(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal", "support"))):
    return serialize(fetch_all("SELECT * FROM support_tickets WHERE workspace_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s", (actor.workspace_id, limit, offset), actor.workspace_id))


@router.post("/support/tickets", status_code=201)
def create_ticket(body: TicketCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal", "support"))):
    ticket_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("INSERT INTO support_tickets(id,workspace_id,opened_by,category,priority,subject,description,related_type,related_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *", (ticket_id, actor.workspace_id, actor.user_id, body.category, body.priority, body.subject, body.description, body.related_type, body.related_id))
        row = cur.fetchone(); audit(cur, actor, "support.ticket.create", "support_ticket", ticket_id, {"category": body.category, "priority": body.priority}, request.state.request_id)
    return serialize(row)
