"""Shared client for the host-side drills: login, price lookup and credit funding.

The API throttles logins per **account** (key login:sha256(email),
LOGIN_RATE_LIMIT_PER_MINUTE, default 10), and several drills authenticate the same
owner back to back, so a legitimate 429 is expected, not a defect. Retry instead of
weakening the control — and wait longer than the window: measured against the live
API, every attempt refreshes the 60s TTL, including the ones that are refused, so
retrying at 12s or 30s intervals keeps the account locked indefinitely. Only 75s of
silence was observed to recover it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
import time
import uuid

import httpx

CREDIT_SKU = "CREDITS_100"
CREDIT_PACK = 100


def totp_code(secret_b32: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    """RFC 6238 written from the specification, not imported from the server's own library.

    The drills need to generate codes for a seed the API hands out, and doing that with pyotp --
    the same package services/api/app/mfa.py verifies with -- would make a green drill compatible
    with nothing: a bug in pyotp's truncation would be invisible on both sides of the wire. This is
    ~10 lines of hmac, and tests/unit/test_mfa_second_factor.py checks it against the eighteen
    published RFC 6238 Appendix B vectors.
    """
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(base64.b32decode(secret_b32), counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


class Codes:
    """Codes for a seed, one per 30 second window.

    accept_mfa_step refuses a step it has already honoured, which is the whole point of the replay
    guard, so a drill that fires two TOTP codes inside the same window gets a legitimate 401 from a
    correct code. Each acceptance therefore waits for the clock to move: at most 30s per step.

    Lives here rather than in one drill because mfa_drill and erasure_drill both spend codes through
    step-up and challenge endpoints, and a second copy of the clock arithmetic is a second thing to
    get wrong.
    """

    def __init__(self, seed: str, used: int = 0):
        self.seed = seed
        self.used = used

    def next(self) -> str:
        step = int(time.time() // 30)
        if step <= self.used:
            time.sleep(30 - (time.time() % 30) + 0.4)
            step = int(time.time() // 30)
        self.used = step
        return totp_code(self.seed, at=step * 30 + 5)


def login(base: str, email: str, password: str, attempts: int = 5, timeout: int = 30):
    """Return (access_token, workspace_id), waiting out the login rate limiter."""
    last = None
    for _ in range(attempts):
        r = httpx.post(base + "/auth/login", json={"email": email, "password": password}, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if "access_token" not in data:
                raise SystemExit(
                    f"{email} has a second factor armed (login returned {sorted(data)}); "
                    "this drill authenticates with a password only -- scripts/mfa_drill.py is the one that knows both")
            return data["access_token"], data["workspaces"][0]["id"]
        last = f"{r.status_code} {r.text[:200]}"
        if r.status_code != 429:
            r.raise_for_status()
        time.sleep(75)
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
