#!/usr/bin/env python3
"""Read one authoritative run and print every figure the release-record faces quote.

Why this exists: the consistency guard (`tests/unit/test_release_record_consistency.py`) recomputes five
structural figures from the tracked archive -- green count, commit sequence, per-run row counts, judged-red
total, per-day split -- so those cannot be written wrong undetected. The figures it *cannot* see are the
ones that live only in per-step logs, which .gitignore keeps off the tree: the step-1 pytest count, the
source-manifest count, the authority-matrix route/write counts, the five drill tallies, the regression and
generic-REST p50/p95, and the browser run's own view/scan/console numbers. Those have been copied from the
previous round's prose often enough that `343 passed` sat in the checklist header for two rounds after the
certified run had printed 382.

So: compute them, print them, and paste nothing you did not just read.

    python scripts/stamp_release_faces.py                 # newest all-green run
    python scripts/stamp_release_faces.py --run 2026...Z  # a named one
    python scripts/stamp_release_faces.py --json          # machine-readable

This script writes nothing on purpose: it is the reader. The writer is
`scripts/release_face_cells.py`, which holds the cell table -- (file, marker line, regex, template) for
every figure above -- and applies it in one gated pass: each cell must resolve to exactly one match or the
whole round is refused, so a face cannot be stamped half a round. What no template can do is say *why* a
figure moved; the sentences around the numbers stay written by whoever read the run.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "release-evidence"
STEP_ROW = re.compile(r"^\s*(?:\d+\s*\|\s*)?([a-z0-9-]+)\s*\|\s*(PASS|FAIL|SKIPPED)\b", re.M)

# The cells the faces quote, keyed by the step whose log carries them. A missing log is reported, not
# guessed at -- an absent reading and a zero reading are different facts.
LOG_FIGURES = {
    "static-verify": [
        ("unit_passed", re.compile(r"(\d+) passed")),
        ("tracked", re.compile(r"tracked=(\d+)")),
        ("listed", re.compile(r"listed=(\d+)")),
        ("routes", re.compile(r"(\d+) routes")),
        ("matrix_writes", re.compile(r"(\d+) writes")),
    ],
    "mfa-drill": [("mfa", re.compile(r"mfa drill: (\d+/\d+)"))],
    "member-drill": [("member", re.compile(r"member drill: (\d+/\d+)"))],
    "erasure-drill": [("erasure", re.compile(r"erasure drill: (\d+/\d+)"))],
    "media-scan-drill": [("media_scan", re.compile(r"media scan drill: (\d+/\d+)"))],
    "report-drill": [("report", re.compile(r"report drill: (\d+/\d+)"))],
    "hold-drill": [("hold", re.compile(r"legal hold drill: (\d+/\d+)"))],
    "market-reconciliation": [("reconcile", re.compile(r"market reconciliation: (\d+/\d+)"))],
    "lease-contention": [("lease", re.compile(r"(\d+ jobs, \d+ workers, [0-9.]+ credits settled once)"))],
    "restore-fidelity-compare": [("restore", re.compile(r"restore fidelity: (.+)"))],
    "provider-regression-100": [
        ("regression", re.compile(r"(\d+/\d+ completed, error rate [\d.]+%)")),
        ("regression_ms", re.compile(r"p50 ([\d.]+)s / p95 ([\d.]+)s")),
    ],
    "generic-rest-roundtrip": [
        ("generic", re.compile(r"(\d+/\d+ completed, error rate [\d.]+%)")),
        ("generic_ms", re.compile(r"p50 ([\d.]+)s / p95 ([\d.]+)s")),
    ],
}
HEADER_KEYS = {
    "started_at": "started_at", "git_commit": "git_commit", "host_load": None,
    "docker_cpus": None, "disk_free_kb": None, "fresh_database": None,
}


def last_ended(text: str, step: str | None) -> str:
    """The finish time the record quotes is the last row's own end column.

    Read from the row's fields rather than by pattern-per-guess: SKIPPED rows carry a different tail
    (`SKIPPED (CAPACITY=1 ...)`), so a pattern that expects a bare status word silently misses them and
    the caller would have quoted a crash as a reading.
    """
    for line in reversed(text.splitlines()):
        fields = [f.strip() for f in line.split("|")]
        if len(fields) >= 2 and step is not None and fields[0].endswith(step):
            return fields[-1] if fields[-1] and fields[-1] != "None" else (fields[-2] or "?")
    return "?"


def runs():
    """Every tracked SUMMARY, oldest first, with its own verdict tally."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "release-evidence/acceptance-*/SUMMARY.txt"],
                         capture_output=True, text=True, check=True).stdout.split()
    for relative in sorted(out):
        text = (ROOT / relative).read_text(encoding="utf-8", errors="replace")
        rows = STEP_ROW.findall(text)
        yield {
            "dir": pathlib.Path(relative).parent,
            "stamp": pathlib.Path(relative).parent.name.replace("acceptance-", ""),
            "commit": (re.search(r"git_commit=(\w+)", text) or [None, "?"])[1],
            "rows": len(rows),
            "pass": sum(1 for _, r in rows if r == "PASS"),
            "skipped": sum(1 for _, r in rows if r == "SKIPPED"),
            "fails": sum(1 for _, r in rows if r == "FAIL"),
            "started": (re.search(r"started_at=(\S+)", text) or [None, "?"])[1],
            "ended": last_ended(text, rows[-1][0] if rows else None),
            "host_load": (re.search(r'host_load="([^"]*)"', text) or [None, None])[1],
            "cpus": (re.search(r"docker_cpus=(\d+)", text) or [None, "?"])[1],
            "disk": (re.search(r"disk_free_kb=(\d+)", text) or [None, "?"])[1],
            "fresh": (re.search(r"fresh_database=(\d)", text) or [None, "?"])[1],
        }


def step_log(run_dir: pathlib.Path, step: str) -> str | None:
    """The log for one step. Number prefixes vary as the chain widens, so match on the name tail."""
    candidates = sorted(run_dir.glob(f"step-*-{step}.log"))
    return candidates[-1].read_text(encoding="utf-8", errors="replace") if candidates else None


def figures(run: dict) -> dict:
    got = {k: run[k] for k in ("stamp", "commit", "rows", "pass", "skipped", "fails", "started",
                               "ended", "host_load", "cpus", "disk", "fresh")}
    run_dir = EVIDENCE / f"acceptance-{run['stamp']}"
    for step, probes in LOG_FIGURES.items():
        text = step_log(run_dir, step)
        for name, pattern in probes:
            if text is None:
                got[name] = "LOG-MISSING"
                continue
            m = pattern.search(text)
            got[name] = m.group(1) if m and m.lastindex == 1 else (m.groups() if m else "NOT-FOUND")
    report = run_dir / "report.json"
    if not report.exists():
        browser = sorted((EVIDENCE).glob(f"browser-a11y-{run['stamp'][:8]}*/report.json"))
        report = browser[-1] if browser else report
    got["browser_report"] = str(report.relative_to(ROOT)) if report.exists() else "MISSING"
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        got["browser_views"] = data.get("views", "NOT-FOUND")
        got["browser_scans"] = data.get("axe_scans", data.get("scans", "NOT-FOUND"))
        got["browser_violations"] = data.get("violations_by_impact", "NOT-FOUND")
        got["browser_commit"] = data.get("git_commit", "NOT-FOUND")
        got["browser_uncaught"] = data.get("uncaught_errors", "NOT-FOUND")
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="stamp of a specific archived run; default is the newest all-green")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    all_runs = list(runs())
    if not all_runs:
        raise SystemExit("no tracked acceptance evidence")
    greens = [r for r in all_runs if r["fails"] == 0]
    if args.run:
        chosen = next((r for r in all_runs if r["stamp"] == args.run), None)
        if chosen is None:
            raise SystemExit(f"{args.run} is not a tracked run; tracked: {[r['stamp'] for r in all_runs]}")
    else:
        if not greens:
            raise SystemExit("no all-green run in the archive -- there is nothing to stamp")
        chosen = max(greens, key=lambda r: r["stamp"])

    got = figures(chosen)
    if args.json:
        print(json.dumps(got, indent=2, default=str))
        return 0
    print(f"authority: acceptance-{chosen['stamp']} (newest all-green of {len(greens)} green, "
          f"{len(all_runs) - len(greens)} red tracked)")
    width = max(len(k) for k in got)
    for key, value in got.items():
        print(f"  {key:<{width}} = {value}")
    unmet = [k for k, v in got.items() if isinstance(v, str) and v in ("LOG-MISSING", "NOT-FOUND", "MISSING")]
    if unmet:
        print(f"\nUNREAD cells: {unmet} -- do not write prose around a cell this script could not read")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
