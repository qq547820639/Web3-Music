#!/usr/bin/env python3
"""Lease contention drill: prove several Workers cannot claim or settle the same job.

complements scripts/chaos_worker_recovery.py, which only kills the single Worker and
checks lease *reclamation*. The `FOR UPDATE SKIP LOCKED` claim in
services/worker/worker.py is never exercised by two live claimants today, so a
double-claim or double-settlement would currently go unnoticed.

verify() refuses to pass unless at least two distinct lease_owner ids actually
claimed jobs in this run; with one claimant the drill would be vacuous.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

ROOT = "http://localhost:8000/api"
STATE = Path(".lease-contention-state.json")
TERMINAL = {"completed", "partial", "failed", "dead_letter", "cancelled"}


def login():
    r = httpx.post(ROOT + "/auth/login", json={"email": "owner@example.local", "password": "demo-owner"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data["access_token"], data["workspaces"][0]["id"]


def request(method, path, token, workspace, **kwargs):
    r = httpx.request(method, ROOT + path,
                      headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace,
                               **(kwargs.pop("headers", None) or {})},
                      timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


def money(value) -> float:
    """Ledger columns are numeric(20,6) and arrive as strings like '20.000000'."""
    return round(float(value), 6)


def fail(msg: str):
    print(f"LEASE CONTENTION FAIL: {msg}", flush=True)
    raise SystemExit(1)


def claimants(job_ids):
    """Distinct worker identities that ever claimed one of these jobs."""
    for job_id in job_ids:
        if not UUID_RE.match(str(job_id)):
            raise SystemExit(f"refusing to query non-uuid job id {job_id!r}")
    quoted = ",".join(f"'{j}'" for j in job_ids)
    sql = ("SELECT DISTINCT lease_owner FROM generation_attempts "
           f"WHERE job_id::text IN ({quoted}) AND lease_owner IS NOT NULL")
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql",
                          "-U", "music_admin", "-d", "music", "-tAc", sql],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"could not read generation_attempts: {out.stderr.strip()[:300]}")
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def prepare(job_count: int):
    token, workspace = login()
    before = request("GET", "/ledger", token, workspace)["balances"]
    jobs = []
    for _ in range(job_count):
        project = request("POST", "/projects", token, workspace, json={"title": "Contention " + uuid.uuid4().hex[:8]})
        quote = request("POST", f"/projects/{project['id']}/quotes", token, workspace,
                        json={"candidate_count": 2, "scenario": "success"})
        job = request("POST", "/jobs", token, workspace,
                      headers={"Idempotency-Key": "contend-" + uuid.uuid4().hex},
                      json={"quote_id": quote["id"], "quote_hash": quote["quote_hash"], "user_confirmation": True})
        jobs.append({"job_id": job["id"], "hold_id": job["hold_id"], "requested": 2})
    STATE.write_text(json.dumps({"token": token, "workspace": workspace, "before": before, "jobs": jobs}))
    print(f"submitted {len(jobs)} jobs for contention", flush=True)


def wait_all(token, workspace, job_ids, timeout):
    end = time.time() + timeout
    while time.time() < end:
        details = [request("GET", f"/jobs/{j}", token, workspace)["job"] for j in job_ids]
        if all(d["status"] in TERMINAL for d in details):
            return details
        time.sleep(1.5)
    stuck = [d["id"] + ":" + d["status"] for d in details if d["status"] not in TERMINAL]
    fail(f"jobs not terminal after {timeout}s: {stuck}")


def verify(timeout: int):
    state = json.loads(STATE.read_text())
    token, workspace, before = state["token"], state["workspace"], state["before"]
    job_ids = [j["job_id"] for j in state["jobs"]]
    details = wait_all(token, workspace, job_ids, timeout)

    by_id = {d["id"]: d for d in details}
    for spec in state["jobs"]:
        d = by_id[spec["job_id"]]
        if d["status"] != "completed":
            fail(f"job {d['id']} ended {d['status']}, expected completed")
        if money(d["settled_credits"]) > money(d["original_amount"]):
            fail(f"job {d['id']} settled {d['settled_credits']} over hold {d['original_amount']} — double settlement")

    # Positive control: contention must actually have happened. generation_jobs.lease_owner
    # is cleared when a job settles, so the per-attempt rows are the record of who claimed.
    owners = claimants(job_ids)
    if len(owners) < 2:
        fail(f"only {len(owners)} distinct worker(s) claimed jobs {owners}; scale workers before prepare")

    # Each job must carry exactly the candidates it asked for, with no duplicates.
    total_settled = 0.0
    for spec in state["jobs"]:
        detail = request("GET", f"/jobs/{spec['job_id']}", token, workspace)
        candidates = detail["candidates"]
        ids = [c["id"] for c in candidates]
        if len(ids) != len(set(ids)):
            fail(f"job {spec['job_id']} has duplicate candidate rows {ids}")
        ready = [c for c in candidates if c["status"] == "ready"]
        if len(ready) != spec["requested"]:
            fail(f"job {spec['job_id']} produced {len(ready)} ready candidates, expected {spec['requested']}")
        if any(len(c.get("sha256") or "") != 64 for c in ready):
            fail(f"job {spec['job_id']} produced a candidate without a media hash")
        total_settled += money(by_id[spec["job_id"]]["settled_credits"])

    after = request("GET", "/ledger", token, workspace)["balances"]
    # The expense account grows as credits settle (test_acceptance.py:57 asserts the same sign).
    spent = money(after["expense"]) - money(before["expense"])
    if spent != total_settled:
        fail(f"ledger expense moved {spent} but jobs settled {total_settled} — settlement double-counted or lost")
    available_moved = money(before["available"]) - money(after["available"])
    if available_moved != total_settled:
        fail(f"available moved {available_moved} but jobs settled {total_settled}")
    if money(after["held"]) != money(before["held"]):
        fail(f"holds left dangling: before={before['held']} after={after['held']}")

    STATE.unlink(missing_ok=True)
    print(f"lease contention passed: {len(details)} jobs, {len(owners)} workers, {total_settled} credits settled once", flush=True)


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "prepare":
        prepare(int(sys.argv[2]) if len(sys.argv) > 2 else 8)
    elif action == "verify":
        verify(int(sys.argv[2]) if len(sys.argv) > 2 else 180)
    else:
        raise SystemExit("usage: lease_contention.py prepare [jobs] | verify [timeout]")
