"""Measure how much wall time sits between the two TTL samples report_drill judges.

`scripts/report_drill.py:360-373` reads the window, files three refusals, reads the window again, and then
requires the second reading to be *strictly smaller*. That is only decidable if a Redis second actually
rolled between the two samples -- and both samples cost a `docker compose exec` round trip, whose latency
this script does not control. This probe runs the real drill with its `redis_cli` timed, and prints the
interval and the two values for each pair, so the question "is the strict inequality a coin flip on this
machine?" gets a reading instead of an argument.

Usage: python .scratch/probe_report_decay.py
"""
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import report_drill as rd  # noqa: E402

records = []
real_redis = rd.redis_cli


def timed(*arguments):
    started = time.monotonic()
    out = real_redis(*arguments)
    records.append({"t": time.monotonic(), "started": started, "args": list(arguments), "out": out})
    return out


rd.redis_cli = timed
rc = rd.main()
print(f"drill rc={rc}", file=sys.stderr)

ttls = [r for r in records if r["args"] and r["args"][0] == "TTL"]
print(f"TTL samples: {len(ttls)}")
by_key = {}
for r in ttls:
    by_key.setdefault(r["args"][1], []).append(r)
for key, samples in by_key.items():
    samples.sort(key=lambda r: r["t"])
    for first, second in zip(samples, samples[1:]):
        try:
            a, b = int(first["out"]), int(second["out"])
        except ValueError:
            continue
        gap = second["started"] - first["started"]
        verdict = "YES" if b < a else "NO -- a correct limiter reads equal when no second rolled"
        print(f"key={key[:46]} window {a} -> {b} spent={a - b} span={gap:.3f}s "
              f"exec1={first['t'] - first['started']:.3f}s exec2={second['t'] - second['started']:.3f}s "
              f"strict_decrease={verdict}")
