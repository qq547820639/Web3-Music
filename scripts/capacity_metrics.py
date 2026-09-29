"""Render the capacity leg's own readings into the line `run_step` copies into SUMMARY.txt.

Extracted from `scripts/capacity-gate-500.sh` so the parsing has a resident test. The first version lived inline and
read `error_rate_pct=1.0` -- that is the *threshold* on the verdict line (`max_error_rate=1.0%`), not the measured
`error_rate=0.000%` the tool printed two lines above it. A number that enters the tracked archive without a control
is a number nobody can trust in the next round, so every field here is anchored to the shape the load tool actually
prints, and a missing field is reported as missing rather than filled with a plausible zero.

Usage: capacity_metrics.py <log-file> <acceptance-rc>
"""
from __future__ import annotations

import pathlib
import re
import sys

# Anchored to the producer's own lines. `(?<![A-Za-z_])` is what keeps `max_error_rate=` out of the measurement.
SCOPE = re.compile(r"^users=(\d+) requests_per_user=(\d+) total=(\d+)", re.M)
RATE = re.compile(r"^elapsed=[\d.]+s throughput=([\d.]+) req/s", re.M)
LATENCY = re.compile(r"^latency_ms p50=([\d.]+) p95=([\d.]+) p99=([\d.]+)", re.M)
ERROR_RATE = re.compile(r"(?<![A-Za-z_])error_rate=([\d.]+)%", re.M)
ACCEPTANCE = re.compile(r"^(\d+) passed\b", re.M)


def readings(text: str) -> dict:
    """Every field this leg can state, with `unset` for anything the transcript does not carry."""
    out = dict.fromkeys(("users", "requests_per_user", "total_requests", "throughput_rps", "p50_ms", "p95_ms",
                         "p99_ms", "error_rate_pct", "acceptance_passed"), "unset")
    if m := SCOPE.search(text):
        out.update(users=m.group(1), requests_per_user=m.group(2), total_requests=m.group(3))
    if m := RATE.search(text):
        out["throughput_rps"] = m.group(1)
    if m := LATENCY.search(text):
        out.update(p50_ms=m.group(1), p95_ms=m.group(2), p99_ms=m.group(3))
    if found := ERROR_RATE.findall(text):
        out["error_rate_pct"] = found[-1]
    if found := ACCEPTANCE.findall(text):
        out["acceptance_passed"] = found[-1]
    return out


def render(text: str, acceptance_rc: str) -> str:
    """The one protocol line for this step, or a refusal naming what is missing -- never a guessed number."""
    got = readings(text)
    missing = [name for name in ("users", "p95_ms", "error_rate_pct") if got[name] == "unset"]
    if missing:
        return f"metric unavailable: the load tool printed no {'/'.join(missing)} reading"
    keys = ("users", "requests_per_user", "total_requests", "p50_ms", "p95_ms", "p99_ms", "throughput_rps",
            "error_rate_pct", "acceptance_passed")
    body = " ".join(f"{key}={got[key]}" for key in keys)
    return f"metric {body} acceptance_rc={acceptance_rc}"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {pathlib.Path(argv[0]).name} <log-file> <acceptance-rc>", file=sys.stderr)
        return 2
    log = pathlib.Path(argv[1])
    if not log.exists():
        print(f"metric unavailable: no log at {log}", file=sys.stderr)
        print(f"metric unavailable: no log at {log}")
        return 0
    print(render(log.read_text(encoding="utf-8", errors="replace"), argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
