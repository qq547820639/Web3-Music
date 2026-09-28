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

WHICH PROVIDER IS USED IS THE STACK'S CHOICE, NOT THIS FILE'S. The batch reports the identity
it actually observed on /api/bootstrap, and an optional third argument refuses the run unless
it matches -- otherwise an overlay that silently failed to apply would still print a pass
against whatever provider the default stack happens to use. The gate asks for 100 *real*
provider regressions; that needs a signed provider contract and API credentials, which cannot
be produced from source. Running the batch with MUSIC_PROVIDER=generic_rest
(scripts/generic-rest-roundtrip.sh) is the closest reproducible substitute: it moves the traffic
through the third-party adapter instead of the emulator's own, and it still is not a contract.

Usage: python scripts/provider_regression.py [jobs] [concurrency] [expect-provider]
"""
from __future__ import annotations

import concurrent.futures
import statistics
import sys
import time
import uuid

import e2e_client
import httpx
import metric_line as metrics

BASE = "http://127.0.0.1:8000/api"
DEFAULT_JOBS = 100
DEFAULT_CONCURRENCY = 6
MEDIA_DOWNLOAD_SAMPLE = 10


def login():
    return e2e_client.login(BASE, "owner@example.local", "demo-owner")


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
              "price": None, "hashes_ok": False, "download_ok": None, "error": None, "job_error": None}
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
        # the reason the WORKER stored, not just the exception this harness caught -- a
        # dead_letter job has no harness exception, so without this the line below reads
        # "error=None" for a job whose row says exactly why it died.
        record["job_error"] = detail.get("job", {}).get("error")
        record["elapsed"] = round(time.time() - started, 2)
        ready = [c for c in detail.get("candidates", []) if c["status"] == "ready"]
        record["candidates"] = len(ready)
        record["settled"] = int(detail["job"]["settled_credits"]) if detail.get("job") else None
        record["hashes_ok"] = bool(ready) and all(len(c.get("sha256") or "") == 64 for c in ready)
        # A terminal failure that stores no reason is a defect in its own right: on 2026-09-25
        # 13 of 15 failed jobs read back as error=NULL because the worker only copied the
        # provider payload, while the real cause lived on the candidate rows. Checked on every
        # job the batch produces, so the property cannot silently regress.
        if record["status"] in {"partial", "failed", "dead_letter"} and not detail["job"].get("error"):
            raise AssertionError(f"job {job['id']} ended {record['status']} with no reason stored")
        if ready and index % max(1, DEFAULT_JOBS // MEDIA_DOWNLOAD_SAMPLE) == 0:
            token = call("POST", f"/candidates/{ready[0]['id']}/media-token")
            audio = CLIENT.get(BASE.replace("/api", "") + token["url"], timeout=60)
            record["download_ok"] = audio.status_code == 200 and len(audio.content) > 1000
    except Exception as exc:  # a single bad job must not abort the batch; it becomes a row
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["elapsed"] = round(time.time() - started, 2)
    return record


def unit_price():
    return e2e_client.unit_price(BASE, TOKEN, WS)


def top_up_credits(needed: float):
    """A batch must not fail because an earlier run drained the demo workspace."""
    e2e_client.ensure_credits(BASE, TOKEN, WS, needed)


def provider_identity() -> tuple[str, str]:
    """What the stack itself says it is talking to, read from /api/bootstrap."""
    snapshot = call("GET", "/bootstrap")["provider"]
    return str(snapshot.get("provider")), str(snapshot.get("approval_status"))


def main(jobs: int, concurrency: int, expect_provider: str | None = None):
    provider_name, approval = provider_identity()
    if expect_provider and provider_name != expect_provider:
        # Without this, an overlay that never took effect would still run -- and pass -- a
        # batch against whichever provider the default stack happens to use.
        raise SystemExit(f"expected provider '{expect_provider}', the stack reports '{provider_name}' "
                         f"(approval {approval}): the overlay did not take effect")
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
            problems.append(f"job #{r['index']} status={r['status']} harness_error={r['error']} job_error={str(r['job_error'])[:160]}")
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
    metrics.emit(completed=len(completed), jobs=jobs, error_rate=error_rate, p50_s=p50, p95_s=p95, credits=total_settled)
    print(f"  provider: {provider_name} (approval {approval})  -- this batch measures the adapter "
          "in front of whatever endpoint the stack points at; a non-emulator name here is still "
          "not a signed provider contract", flush=True)
    for line in problems[:15]:
        print(f"  FAIL: {line}", flush=True)
    if len(problems) > 15:
        print(f"  ... {len(problems) - 15} more", flush=True)
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JOBS,
         int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CONCURRENCY,
         sys.argv[3] if len(sys.argv) > 3 else None)
