#!/usr/bin/env python3
"""Dependency-light concurrent HTTP verifier for the 500-user capacity gate.

Examples:
  python scripts/load-test-500.py --base-url http://localhost:8080 --path /health
  python scripts/load-test-500.py --base-url http://localhost:8080 --path /api/projects \
      --email owner@example.local --password demo-owner --users 500 --requests-per-user 2
"""
from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import sys
import time
from collections import Counter

import httpx


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
    return ordered[idx]


async def login(client: httpx.AsyncClient, email: str, password: str) -> tuple[str, str | None]:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    data = response.json()
    workspaces = data.get("workspaces") or []
    workspace_id = str(workspaces[0].get("workspace_id") or workspaces[0].get("id")) if workspaces else None
    return data["access_token"], workspace_id


async def run(args) -> int:
    limits = httpx.Limits(max_connections=max(args.users + 50, 100), max_keepalive_connections=max(args.users, 100))
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
    headers = {"User-Agent": "resonance-capacity-gate/14"}
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), limits=limits, timeout=timeout, headers=headers) as client:
        token = args.token
        workspace_id = args.workspace_id
        if args.email:
            token, discovered_workspace = await login(client, args.email, args.password)
            workspace_id = workspace_id or discovered_workspace
        request_headers = {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        if workspace_id:
            request_headers["X-Workspace-Id"] = workspace_id

        latencies: list[float] = []
        statuses: Counter[int | str] = Counter()
        gate = asyncio.Event()

        async def one_user(user_index: int):
            await gate.wait()
            for request_index in range(args.requests_per_user):
                started = time.perf_counter()
                try:
                    response = await client.request(args.method, args.path, headers=request_headers)
                    statuses[response.status_code] += 1
                except Exception as exc:
                    statuses[type(exc).__name__] += 1
                finally:
                    latencies.append((time.perf_counter() - started) * 1000.0)
                if args.think_time > 0 and request_index + 1 < args.requests_per_user:
                    await asyncio.sleep(args.think_time)

        tasks = [asyncio.create_task(one_user(i)) for i in range(args.users)]
        started = time.perf_counter()
        gate.set()
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - started

    total = sum(statuses.values())
    success = sum(v for k, v in statuses.items() if isinstance(k, int) and 200 <= k < 400)
    errors = total - success
    error_rate = (errors / total * 100.0) if total else 100.0
    rps = total / elapsed if elapsed else 0.0
    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)
    p99 = percentile(latencies, 0.99)

    print("Resonance 500-user capacity result")
    print(f"target={args.method} {args.base_url.rstrip('/')}{args.path}")
    print(f"users={args.users} requests_per_user={args.requests_per_user} total={total}")
    print(f"elapsed={elapsed:.3f}s throughput={rps:.1f} req/s")
    print(f"latency_ms p50={p50:.1f} p95={p95:.1f} p99={p99:.1f} mean={statistics.fmean(latencies) if latencies else 0:.1f}")
    print(f"statuses={dict(statuses)} error_rate={error_rate:.3f}%")

    passed = error_rate <= args.max_error_rate and p95 <= args.max_p95_ms
    print(f"gate={'PASS' if passed else 'FAIL'} max_error_rate={args.max_error_rate}% max_p95_ms={args.max_p95_ms}")
    return 0 if passed else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8080")
    p.add_argument("--path", default="/health")
    p.add_argument("--method", default="GET", choices=["GET", "HEAD"])
    p.add_argument("--users", type=int, default=500)
    p.add_argument("--requests-per-user", type=int, default=1)
    p.add_argument("--think-time", type=float, default=0.0)
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--max-error-rate", type=float, default=1.0)
    p.add_argument("--max-p95-ms", type=float, default=800.0)
    p.add_argument("--email")
    p.add_argument("--password", default="")
    p.add_argument("--token")
    p.add_argument("--workspace-id")
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    if args.users < 1 or args.requests_per_user < 1:
        raise SystemExit("users and requests-per-user must be >= 1")
    if args.email and not args.password:
        raise SystemExit("--password is required with --email")
    sys.exit(asyncio.run(run(args)))
