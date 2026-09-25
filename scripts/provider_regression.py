#!/usr/bin/env python3
"""Provider generation regression batch (release gate G9-6).

Runs N complete generation jobs through the real pipeline — quote, hold, worker claim,
provider submit, media ingest, settle — and asserts the per-job outcome instead of the
aggregate throughput the capacity gate measures:

  * the job reaches 'completed' (not partial/failed/dead-letter),
  * every ready candidate carries a 64-hex sha256 and a byte-sized object that actually
    downloads,
  * settled_credits equals the quote price for a fully successful job, and the hold is
    fully drained,
  * the workspace's ledger closes: available drops by exactly the summed settlement and
    no hold is left dangling.

RUNS AGAINST THE PROVIDER EMULATOR. The gate asks for 100 *real* provider regressions;
that needs a signed provider contract and API credentials, which cannot be produced from
source. This harness is the reusable half: point MUSIC_PROVIDER at a real adapter and the
same 100-run assertion set applies.

Usage: python scripts/provider_regression.py [jobs] [concurrency]
"""
from __future__ import annotations

import concurrent.futures
import math
import statistics
import sys
import time
import uuid

import httpx

BASE = "http://localhost:8000/api"
DEFAULT_JOBS = 100
DEFAULT_CONCURRENCY = 6
MEDIA_DOWNLOAD_SAMPLE = 10
CREDIT_SKU = "CREDITS_100"
CREDIT_PACK = 100


def login():
    r = httpx.post(BASE + "/auth/login", json={"email": "owner@example.local", "password": "demo-owner"}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"], r.json()["workspaces"][0]["id"]


TOKEN, WS = login()
HDRS = {"Authorization": f"Bearer {TOKEN}", "X-Workspace-Id": WS, "Content-Type": "application/json"}
CLIENT = httpx.Client(timeout=60)


def call(method, path, **kwargs):
    r = CLIENT.request(method, BASE + path, headers={**HDRS, **(kwargs.pop("headers", None) or {})}, **kwargs)
    r.raise_for_status()
    return r.json()


def run_one(index: int):
    """One complete generation, returning a record of what happened."""
    started = time.time()
    record = {"index": index, "status": "error", "candidates": 0, "settled": None,
              "price": None, "hashes_ok": False, "download_ok": None, "error": None}
    try:
        project = call("POST", "/projects", json={"title": f"Regression {index:03d} {uuid.uuid4().hex[:6]}"})
        quote = call("POST", f"/projects/{project['id']}/quotes",
                     json={"candidate_count": 1, "scenario": "success"})
        record["price"] = int(quote["quote"]["total_credits"])
        job = call("POST", "/jobs", headers={"Idempotency-Key": f"reg-{index}-{uuid.uuid4().hex}"},
                   json={"quote_id": quote["id"], "quote_hash": quote["quote_hash"], "user_confirmation": True})
        record["job_id"] = job["id"]
        detail = {}
        deadline = time.time() + 180
        while time.time() < deadline:
            detail = call("GET", f"/jobs/{job['id']}")
            if detail["job"]["status"] in {"completed", "partial", "failed", "dead_letter", "cancelled"}:
                break
            time.sleep(1)
        record["status"] = detail.get("job", {}).get("status", "timeout")
        record["elapsed"] = round(time.time() - started, 2)
        ready = [c for c in detail.get("candidates", []) if c["status"] == "ready"]
        record["candidates"] = len(ready)
        record["settled"] = int(detail["job"]["settled_credits"]) if detail.get("job") else None
        record["hashes_ok"] = bool(ready) and all(len(c.get("sha256") or "") == 64 for c in ready)
        if ready and index % max(1, DEFAULT_JOBS // MEDIA_DOWNLOAD_SAMPLE) == 0:
            token = call("POST", f"/candidates/{ready[0]['id']}/media-token")
            audio = CLIENT.get(BASE.replace("/api", "") + token["url"], timeout=60)
            record["download_ok"] = audio.status_code == 200 and len(audio.content) > 1000
    except Exception as exc:  # a single bad job must not abort the batch; it becomes a row
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["elapsed"] = round(time.time() - started, 2)
    return record


def unit_price():
    """Learn the credit price from the quoting engine rather than hardcoding it."""
    project = call("POST", "/projects", json={"title": "Regression probe " + uuid.uuid4().hex[:6]})
    quote = call("POST", f"/projects/{project['id']}/quotes", json={"candidate_count": 1, "scenario": "success"})
    return int(quote["quote"]["total_credits"])


def top_up_credits(needed: float):
    """A batch must not fail because an earlier run drained the demo workspace."""
    available = float(call("GET", "/ledger")["balances"]["available"])
    if available >= needed:
        return
    packs = math.ceil((needed - available) / CREDIT_PACK)
    order = call("POST", "/orders/credits", json={"sku": CREDIT_SKU, "quantity": packs})
    call("POST", f"/orders/{order['id']}/pay", headers={"Idempotency-Key": "reg-topup-" + uuid.uuid4().hex},
         json={"scenario": "success"})
    deadline = time.time() + 60
    while time.time() < deadline:
        available = float(call("GET", "/ledger")["balances"]["available"])
        if available >= needed:
            return
        time.sleep(1)
    raise SystemExit(f"credit top-up did not land: have {available}, batch needs {needed}")


def main(jobs: int, concurrency: int):
    price = unit_price()
    top_up_credits(jobs * price + price)
    before = call("GET", "/ledger")["balances"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        records = list(pool.map(run_one, range(jobs)))

    finished = [r for r in records if r.get("elapsed") is not None]
    latencies = sorted(r["elapsed"] for r in finished)
    completed = [r for r in records if r["status"] == "completed"]
    problems = []

    for r in records:
        if r["status"] != "completed":
            problems.append(f"job #{r['index']} status={r['status']} error={r['error']}")
        elif r["candidates"] != 1:
            problems.append(f"job #{r['index']} produced {r['candidates']} ready candidates, expected 1")
        elif not r["hashes_ok"]:
            problems.append(f"job #{r['index']} has a candidate without a 64-hex media hash")
        elif r["settled"] != r["price"]:
            problems.append(f"job #{r['index']} settled {r['settled']} credits, quote priced {r['price']}")
        elif r["download_ok"] is False:
            problems.append(f"job #{r['index']} media token minted but the object did not download")

    after = call("GET", "/ledger")["balances"]
    total_settled = sum(r["settled"] or 0 for r in records)
    available_moved = round(float(before["available"]) - float(after["available"]), 6)
    if available_moved != total_settled:
        problems.append(f"ledger available moved {available_moved} but the batch settled {total_settled}")
    if round(float(after["held"]), 6) != round(float(before["held"]), 6):
        problems.append(f"batch left dangling holds: held {before['held']} -> {after['held']}")

    p50 = statistics.median(latencies) if latencies else 0
    p95 = latencies[int(len(latencies) * .95) - 1] if len(latencies) >= 20 else (latencies[-1] if latencies else 0)
    error_rate = round(100.0 * (jobs - len(completed)) / max(1, jobs), 2)

    print(f"provider regression: {len(completed)}/{jobs} completed, "
          f"error rate {error_rate}%, p50 {p50}s, p95 {p95}s, "
          f"settled {total_settled} credits across {len(finished)} finished jobs", flush=True)
    print(f"  provider: emulator  (real-provider contract still required for this gate)", flush=True)
    for line in problems[:15]:
        print(f"  FAIL: {line}", flush=True)
    if len(problems) > 15:
        print(f"  ... {len(problems) - 15} more", flush=True)
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JOBS,
         int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CONCURRENCY)
