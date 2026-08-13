from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import Actor, require_platform_admin, require_roles
from ..common import audit, serialize
from ..db import fetch_all, fetch_one, transaction

router = APIRouter(prefix="/api/admin/v12", tags=["Operations Control Plane"])


class EvidenceUpdate(BaseModel):
    status: str = Field(pattern="^(missing|in_progress|passed|waived|failed)$")
    evidence: dict[str, Any] = Field(default_factory=dict)
    owner: str = Field(min_length=1, max_length=120)


class TicketUpdate(BaseModel):
    status: str = Field(pattern="^(open|waiting_customer|waiting_internal|resolved|closed)$")
    assigned_to: str | None = None


@router.get("/dashboard")
def dashboard(actor: Actor = Depends(require_roles("owner", "admin", "billing", "legal", "support"))):
    counts = fetch_one(
        """
        SELECT
          (SELECT count(*) FROM song_projects WHERE workspace_id=%s) projects,
          (SELECT count(*) FROM generation_jobs WHERE workspace_id=%s) jobs,
          (SELECT count(*) FROM generation_jobs WHERE workspace_id=%s AND status IN ('failed','dead_letter')) failed_jobs,
          (SELECT count(*) FROM asset_snapshots WHERE workspace_id=%s) assets,
          (SELECT count(*) FROM orders WHERE workspace_id=%s) orders,
          (SELECT count(*) FROM orders WHERE workspace_id=%s AND status='fulfilled') fulfilled_orders,
          (SELECT count(*) FROM licenses WHERE seller_workspace_id=%s OR buyer_workspace_id=%s) licenses,
          (SELECT count(*) FROM moderation_cases WHERE workspace_id=%s AND status NOT IN ('resolved','dismissed')) open_cases,
          (SELECT count(*) FROM support_tickets WHERE workspace_id=%s AND status NOT IN ('resolved','closed')) open_tickets
        """,
        (actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id),
        actor.workspace_id,
    )
    ledger = fetch_all("SELECT account_type,balance FROM ledger_balances WHERE workspace_id=%s ORDER BY account_type", (actor.workspace_id,), actor.workspace_id)
    provider = fetch_one(
        "SELECT provider,approval_status,contract_version,captured_at,snapshot FROM provider_capability_snapshots ORDER BY captured_at DESC LIMIT 1"
    )
    payments = fetch_one(
        "SELECT COALESCE(sum(amount) FILTER (WHERE status IN ('succeeded','partially_refunded','refunded')),0) gross,COALESCE(sum(refunded_amount),0) refunded,count(*) FILTER (WHERE status='failed') failed FROM payments WHERE workspace_id=%s",
        (actor.workspace_id,), actor.workspace_id,
    )
    quality = fetch_one(
        "SELECT COALESCE(avg(score),0) avg_score,count(*) evaluations FROM quality_evaluations WHERE workspace_id=%s AND created_at>=%s",
        (actor.workspace_id, datetime.now(timezone.utc) - timedelta(days=28)), actor.workspace_id,
    )
    return serialize({"counts": counts, "ledger": ledger, "payments": payments, "quality": quality, "provider": provider})


@router.get("/reconciliation")
def reconciliation(actor: Actor = Depends(require_roles("owner", "admin", "billing"))):
    checks = fetch_one(
        """
        SELECT
          (SELECT COALESCE(sum(e.delta),0) FROM ledger_entries e WHERE e.workspace_id=%s) AS ledger_net,
          (SELECT count(*) FROM credit_holds WHERE workspace_id=%s AND settled_amount+released_amount>original_amount) AS invalid_holds,
          (SELECT count(*) FROM payments WHERE workspace_id=%s AND refunded_amount>amount) AS invalid_payments,
          (SELECT count(*) FROM asset_snapshots a LEFT JOIN media_assets m ON m.sha256=a.media_hash AND m.workspace_id=a.workspace_id WHERE a.workspace_id=%s AND m.id IS NULL) AS missing_media,
          (SELECT count(*) FROM rights_manifests r LEFT JOIN asset_snapshots a ON a.id=r.asset_snapshot_id WHERE r.workspace_id=%s AND a.id IS NULL) AS orphan_manifests
        """,
        (actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id, actor.workspace_id), actor.workspace_id,
    )
    passed = float(checks["ledger_net"]) == 0 and all(int(checks[k]) == 0 for k in ("invalid_holds", "invalid_payments", "missing_media", "orphan_manifests"))
    return serialize({"status": "passed" if passed else "failed", "checks": checks, "checked_at": datetime.now(timezone.utc)})


@router.get("/payments")
def payments(actor: Actor = Depends(require_roles("owner", "admin", "billing"))):
    return serialize(fetch_all(
        "SELECT p.*,o.order_number,o.order_type,o.status AS order_status FROM payments p JOIN orders o ON o.id=p.order_id WHERE p.workspace_id=%s ORDER BY p.created_at DESC",
        (actor.workspace_id,), actor.workspace_id,
    ))


@router.get("/moderation")
def moderation(actor: Actor = Depends(require_roles("owner", "admin", "legal", "support"))):
    cases = fetch_all("SELECT * FROM moderation_cases WHERE workspace_id=%s ORDER BY created_at DESC", (actor.workspace_id,), actor.workspace_id)
    tickets = fetch_all("SELECT * FROM support_tickets WHERE workspace_id=%s ORDER BY created_at DESC", (actor.workspace_id,), actor.workspace_id)
    return serialize({"cases": cases, "tickets": tickets})


@router.put("/tickets/{ticket_id}")
def update_ticket(ticket_id: str, body: TicketUpdate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "support"))):
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("UPDATE support_tickets SET status=%s,assigned_to=%s,updated_at=now() WHERE id=%s AND workspace_id=%s RETURNING *", (body.status, body.assigned_to, ticket_id, actor.workspace_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "ticket not found")
        audit(cur, actor, "support.ticket.update", "support_ticket", ticket_id, {"status": body.status}, request.state.request_id)
    return serialize(row)


@router.get("/release-evidence")
def release_evidence(actor: Actor = Depends(require_platform_admin)):
    return serialize(fetch_all("SELECT * FROM release_evidence ORDER BY gate,evidence_key"))


@router.put("/release-evidence/{gate}/{evidence_key}")
def update_release_evidence(gate: str, evidence_key: str, body: EvidenceUpdate, request: Request, actor: Actor = Depends(require_platform_admin)):
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO release_evidence(gate,evidence_key,status,evidence,owner) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(gate,evidence_key) DO UPDATE SET status=EXCLUDED.status,evidence=EXCLUDED.evidence,owner=EXCLUDED.owner,updated_at=now() RETURNING *",
            (gate, evidence_key, body.status, psycopg2.extras.Json(body.evidence), body.owner),
        )
        row = cur.fetchone(); audit(cur, actor, "release_evidence.update", "release_evidence", f"{gate}:{evidence_key}", {"status": body.status}, request.state.request_id)
    return serialize(row)


@router.get("/release-gate/{gate}")
def release_gate(gate: str, actor: Actor = Depends(require_platform_admin)):
    rows = fetch_all("SELECT * FROM release_evidence WHERE gate=%s ORDER BY evidence_key", (gate,))
    blockers = [r for r in rows if r["status"] not in {"passed", "waived"}]
    return serialize({"gate": gate, "status": "pass" if rows and not blockers else "blocked", "blockers": blockers, "evidence": rows})

class PayoutUpdate(BaseModel):
    status: str = Field(pattern="^(pending|processing|paid|failed|reversed)$")
    provider_payout_id: str | None = Field(default=None, max_length=255)


@router.get("/payouts")
def payout_queue(actor: Actor = Depends(require_platform_admin)):
    return serialize(fetch_all("SELECT * FROM platform_payout_queue()"))


@router.put("/payouts/{payout_id}")
def update_payout(payout_id: str, body: PayoutUpdate, request: Request, actor: Actor = Depends(require_platform_admin)):
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM set_platform_payout_status(%s,%s,%s)", (payout_id, body.status, body.provider_payout_id))
        row = cur.fetchone()
        audit(cur, actor, "payout.status.update", "payout", payout_id, {"status": body.status}, request.state.request_id)
    return serialize(row)
