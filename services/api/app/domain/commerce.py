from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg2.extras


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def order_number(order_id: str) -> str:
    return f"RZN-{datetime.now(timezone.utc):%Y%m%d}-{order_id.replace('-', '')[:10].upper()}"


def issue_credits(cur, workspace_id: str, credits: int, operation_key: str, reference_id: str, created_by: str) -> str:
    if credits <= 0:
        raise ValueError("credits must be positive")
    cur.execute("SELECT id FROM ledger_transactions WHERE workspace_id=%s AND operation_key=%s FOR UPDATE", (workspace_id, operation_key))
    existing = cur.fetchone()
    if existing:
        return str(existing["id"] if isinstance(existing, dict) else existing[0])
    cur.execute("SELECT id,account_type FROM ledger_accounts WHERE workspace_id=%s AND account_type IN ('available','issued') FOR UPDATE", (workspace_id,))
    accounts = {r["account_type"]: str(r["id"]) for r in cur.fetchall()}
    tx_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO ledger_transactions(id,workspace_id,operation_key,transaction_type,reference_type,reference_id,metadata,created_by) VALUES(%s,%s,%s,'adjustment','payment',%s,%s,%s)",
        (tx_id, workspace_id, operation_key, reference_id, psycopg2.extras.Json({"credits": credits}), created_by),
    )
    cur.execute(
        "INSERT INTO ledger_entries(workspace_id,transaction_id,account_id,delta) VALUES(%s,%s,%s,%s),(%s,%s,%s,%s)",
        (workspace_id, tx_id, accounts["available"], credits, workspace_id, tx_id, accounts["issued"], -credits),
    )
    return tx_id


def rights_allow(manifest: dict, capability: str) -> bool:
    return manifest.get("capabilities", {}).get(capability, {}).get("status") == "allowed"


def assert_offer_rights(manifest: dict) -> None:
    if not rights_allow(manifest, "commercial_use") or not rights_allow(manifest, "license"):
        raise ValueError("rights manifest does not permit commercial licensing")
    if manifest.get("legal_hold") or manifest.get("status") in {"restricted", "disputed", "blocked"}:
        raise ValueError("asset is restricted or under legal hold")


def revoke_credits(cur, workspace_id: str, credits: int, operation_key: str, reference_id: str, created_by: str) -> str:
    if credits <= 0:
        raise ValueError("credits must be positive")
    cur.execute("SELECT id FROM ledger_transactions WHERE workspace_id=%s AND operation_key=%s FOR UPDATE", (workspace_id, operation_key))
    existing = cur.fetchone()
    if existing:
        return str(existing["id"] if isinstance(existing, dict) else existing[0])
    cur.execute("SELECT account_type,balance FROM ledger_balances WHERE workspace_id=%s", (workspace_id,))
    balances = {r["account_type"]: float(r["balance"]) for r in cur.fetchall()}
    if balances.get("available", 0) < credits:
        raise ValueError("purchased credits have already been consumed")
    cur.execute("SELECT id,account_type FROM ledger_accounts WHERE workspace_id=%s AND account_type IN ('available','issued') FOR UPDATE", (workspace_id,))
    accounts = {r["account_type"]: str(r["id"]) for r in cur.fetchall()}
    tx_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO ledger_transactions(id,workspace_id,operation_key,transaction_type,reference_type,reference_id,metadata,created_by) VALUES(%s,%s,%s,'refund','payment',%s,%s,%s)",
        (tx_id, workspace_id, operation_key, reference_id, psycopg2.extras.Json({"credits_revoked": credits}), created_by),
    )
    cur.execute(
        "INSERT INTO ledger_entries(workspace_id,transaction_id,account_id,delta) VALUES(%s,%s,%s,%s),(%s,%s,%s,%s)",
        (workspace_id, tx_id, accounts["available"], -credits, workspace_id, tx_id, accounts["issued"], credits),
    )
    return tx_id
