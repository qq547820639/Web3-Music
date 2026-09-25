"""Shared login helper for the host-side drills.

The API rate-limits logins per IP (LOGIN_RATE_LIMIT_PER_MINUTE, default 10), and several
drills run back to back inside one acceptance minute, so a legitimate 429 is expected, not
a defect. Retry with backoff rather than weakening the control.
"""
from __future__ import annotations

import time

import httpx


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
