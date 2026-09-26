#!/usr/bin/env python3
"""Destructive lease recovery drill for a running local Docker stack."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = "http://127.0.0.1:8000/api"
STATE = Path(".chaos-state.json")


def login():
    r = httpx.post(ROOT + "/auth/login", json={"email": "owner@example.local", "password": "demo-owner"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data["access_token"], data["workspaces"][0]["id"]


def headers(token, workspace, extra=None):
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace, **(extra or {})}


def request(method, path, token, workspace, **kwargs):
    r = httpx.request(method, ROOT + path, headers=headers(token, workspace, kwargs.pop("headers", None)), timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


def submit():
    token, workspace = login()
    before = request("GET", "/ledger", token, workspace)["balances"]
    project = request("POST", "/projects", token, workspace, json={"title": "Lease recovery " + uuid.uuid4().hex[:8]})
    quote = request("POST", f"/projects/{project['id']}/quotes", token, workspace, json={"candidate_count": 1, "scenario": "timeout"})
    job = request("POST", "/jobs", token, workspace, headers={"Idempotency-Key": "chaos-" + uuid.uuid4().hex}, json={"quote_id": quote["id"], "quote_hash": quote["quote_hash"], "user_confirmation": True})
    end = time.time() + 25
    attempt = 0
    while time.time() < end:
        detail = request("GET", f"/jobs/{job['id']}", token, workspace)
        attempt = int(detail["job"].get("attempt_count") or 0)
        if detail["job"]["status"] in {"submitting", "processing", "ingesting"}:
            break
        time.sleep(.5)
    else:
        raise SystemExit("job never entered a leased processing state")
    STATE.write_text(json.dumps({"token": token, "workspace": workspace, "job": job["id"], "attempt": attempt, "before": before}))
    print(job["id"])


def verify():
    state = json.loads(STATE.read_text())
    token, workspace, job_id = state["token"], state["workspace"], state["job"]
    end = time.time() + 55
    recovered = None
    while time.time() < end:
        detail = request("GET", f"/jobs/{job_id}", token, workspace)
        job = detail["job"]
        if int(job.get("attempt_count") or 0) > int(state["attempt"]):
            recovered = job
            break
        time.sleep(1)
    if not recovered:
        raise SystemExit("expired lease was not reclaimed by the restarted worker")
    request("POST", f"/admin/jobs/{job_id}/cancel", token, workspace)
    end = time.time() + 45
    while time.time() < end:
        job = request("GET", f"/jobs/{job_id}", token, workspace)["job"]
        if job["status"] == "cancelled":
            break
        time.sleep(1)
    else:
        raise SystemExit("recovered job did not cancel")
    after = request("GET", "/ledger", token, workspace)["balances"]
    if float(after["held"]) != float(state["before"]["held"]) or float(after["available"]) != float(state["before"]["available"]):
        raise SystemExit(f"lease recovery leaked credits: before={state['before']} after={after}")
    STATE.unlink(missing_ok=True)
    print("worker lease recovery passed")


if __name__ == "__main__":
    {"submit": submit, "verify": verify}[sys.argv[1]]()
