"""Shared client for the host-side drills: login, price lookup and credit funding.

The API rate-limits logins per IP (LOGIN_RATE_LIMIT_PER_MINUTE, default 10), and several
drills run back to back inside one acceptance minute, so a legitimate 429 is expected, not
a defect. Retry with backoff rather than weakening the control.
"""
from __future__ import annotations

import math
import time
import uuid

import httpx

CREDIT_SKU = "CREDITS_100"
CREDIT_PACK = 100


def login(base: str, email: str, password: str, attempts: int = 8, timeout: int = 30):
    """Return (access_token, workspace_id), waiting out the login rate limiter."""
    last = None
    for attempt in range(attempts):
        r = httpx.post(base + "/auth/login", json={"email": email, "password": password}, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return data["access_token"], data["workspaces"][0]["id"]
        last = f"{r.status_code} {r.text[:200]}"
        if r.status_code != 429:
            r.raise_for_status()
        # The window is per minute, so a short sleep is enough for the first retry or two;
        # back off further if the bucket is already saturated by neighbouring steps.
        time.sleep(min(70, 12 * (attempt + 1)))
    raise SystemExit(f"login for {email} kept getting rate limited: {last}")


def headers(token, workspace):
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace,
            "Content-Type": "application/json"}


def available_credits(base, token, workspace):
    r = httpx.get(base + "/ledger", headers=headers(token, workspace), timeout=30)
    r.raise_for_status()
    return float(r.json()["balances"]["available"])


def unit_price(base, token, workspace):
    """Learn the per-job credit price from the quoting engine instead of hardcoding it."""
    h = headers(token, workspace)
    project = httpx.post(base + "/projects", json={"title": "probe " + uuid.uuid4().hex[:8]}, headers=h, timeout=40)
    project.raise_for_status()
    quote = httpx.post(base + f"/projects/{project.json()['id']}/quotes",
                       json={"candidate_count": 1, "scenario": "success"}, headers=h, timeout=40)
    quote.raise_for_status()
    return int(quote.json()["quote"]["total_credits"])


def ensure_credits(base, token, workspace, needed, timeout=60):
    """Buy enough credit packs that the tenant can spend `needed`, then wait for them."""
    have = available_credits(base, token, workspace)
    if have >= needed:
        return have
    packs = max(1, math.ceil((needed - have) / CREDIT_PACK))
    h = headers(token, workspace)
    order = httpx.post(base + "/orders/credits", json={"sku": CREDIT_SKU, "quantity": packs}, headers=h, timeout=40)
    order.raise_for_status()
    order_id = order.json()["id"]
    pay = httpx.post(base + f"/orders/{order_id}/pay", json={"scenario": "success"},
                     headers={**h, "Idempotency-Key": "fund-" + uuid.uuid4().hex}, timeout=40)
    pay.raise_for_status()
    deadline = time.time() + timeout
    while time.time() < deadline:
        have = available_credits(base, token, workspace)
        if have >= needed:
            return have
        time.sleep(1)
    raise SystemExit(f"credit top-up did not land: have {available_credits(base, token, workspace)}, need {needed}")
