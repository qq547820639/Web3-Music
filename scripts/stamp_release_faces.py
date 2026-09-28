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
import datetime as dt
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
    "restore-fidelity-compare": [("restore", re.compile(r"restore fidelity passed: (.+)"))],
    "provider-regression-100": [
        ("regression", re.compile(r"(\d+/\d+ completed, error rate [\d.]+%)")),
        ("regression_ms", re.compile(r"p50 ([\d.]+)s, p95 ([\d.]+)s")),
    ],
    "generic-rest-roundtrip": [
        ("generic", re.compile(r"(\d+/\d+ completed, error rate [\d.]+%)")),
        ("generic_ms", re.compile(r"p50 ([\d.]+)s, p95 ([\d.]+)s")),
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


def summarize(text: str, directory: pathlib.Path) -> dict:
    """One SUMMARY's own header and row table, as the faces quote it."""
    rows = STEP_ROW.findall(text)
    return {
        "dir": directory,
        "stamp": directory.name.replace("acceptance-", ""),
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


def run_from_disk(stamp: str) -> dict:
    """A run that exists on this host but is not committed yet -- a chain mid-flight, or one being closed out.

    The tracked archive is the denominator for every census, but the census script has to be callable from
    inside the chain that produces the evidence, before `git add` has happened. Same parser, same fields, so
    a reading taken here cannot differ in shape from the one the faces will take after staging.
    """
    path = EVIDENCE / f"acceptance-{stamp}" / "SUMMARY.txt"
    if not path.exists():
        raise SystemExit(f"{path} does not exist on this host")
    return summarize(path.read_text(encoding="utf-8", errors="replace"), path.parent)


def runs():
    """Every tracked SUMMARY, oldest first, with its own verdict tally."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "release-evidence/acceptance-*/SUMMARY.txt"],
                         capture_output=True, text=True, check=True).stdout.split()
    for relative in sorted(out):
        path = ROOT / relative
        yield summarize(path.read_text(encoding="utf-8", errors="replace"), path.parent)


def step_log(run_dir: pathlib.Path, step: str) -> str | None:
    """The log for one step. Number prefixes vary as the chain widens, so match on the name tail."""
    candidates = sorted(run_dir.glob(f"step-*-{step}.log"))
    return candidates[-1].read_text(encoding="utf-8", errors="replace") if candidates else None


def rows_by_name(run_dir: pathlib.Path) -> dict:
    """Which row of the archived record each step is, as the faces count it ("流水线第 N 步").

    Read off the SUMMARY's own row order, not off `acceptance-all.sh`: a step dispatched inside a
    conditional (`browser-a11y`) has no line starting the file's `run_step` column, so parsing the script
    silently numbers everything after it one row early.
    """
    text = (run_dir / "SUMMARY.txt").read_text(encoding="utf-8", errors="replace")
    out = {}
    for index, (step, _result) in enumerate(STEP_ROW.findall(text), start=1):
        out.setdefault(step, index)
    return out


def step_window(run_dir: pathlib.Path, step: str) -> tuple[str, str] | None:
    """The SUMMARY's own started/finished stamps for one row, as written in its table."""
    text = (run_dir / "SUMMARY.txt").read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4 and parts[0] == step:
            return parts[-2], parts[-1]
    return None


def browser_pair(run: dict) -> pathlib.Path | None:
    """The a11y report that belongs to this acceptance run, judged by the run's own row window.

    The old rule was a date glob plus `[-1]`, which handed the faces whatever browser run happened to be
    the newest that day -- and on 2026-09-28 a second a11y leg (a later chain, a different tree) moved a
    certified run's quoted figures onto a report stamped with another commit, while the hidden-element
    census shifted by one. So both keys have to agree: the `git_commit` the report carries must equal the
    one the SUMMARY records, and its `generated_at` must fall inside the `browser-a11y` row's own window.
    When nothing satisfies both, the answer is "no report", not "the closest one".
    """
    window = step_window(EVIDENCE / f"acceptance-{run['stamp']}", "browser-a11y")
    if not window or not run["commit"]:
        return None
    low, high = window
    span = (dt.datetime.strptime(low, "%Y-%m-%dT%H:%M:%SZ"),
            dt.datetime.strptime(high, "%Y-%m-%dT%H:%M:%SZ") + dt.timedelta(minutes=5))
    candidates = []
    for report in sorted(EVIDENCE.glob("browser-a11y-*/report.json")):
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            made = dt.datetime.strptime(str(data.get("generated_at")), "%Y-%m-%dT%H:%M:%SZ")
        except (OSError, ValueError, TypeError):
            continue
        if str(data.get("git_commit", "")) == run["commit"] and span[0] <= made <= span[1]:
            candidates.append(report)
    return candidates[-1] if candidates else None


def figures(run: dict) -> dict:
    got = {k: run[k] for k in ("stamp", "commit", "rows", "pass", "skipped", "fails", "started",
                               "ended", "host_load", "cpus", "disk", "fresh")}
    run_dir = EVIDENCE / f"acceptance-{run['stamp']}"
    got["rows_by_name"] = rows_by_name(run_dir)
    for step, probes in LOG_FIGURES.items():
        text = step_log(run_dir, step)
        for name, pattern in probes:
            if text is None:
                got[name] = "LOG-MISSING"
                continue
            m = pattern.search(text)
            got[name] = m.group(1) if m and m.lastindex == 1 else (m.groups() if m else "NOT-FOUND")
    for name, step in (("mfa_step", "mfa-drill"), ("media_scan_step", "media-scan-drill"),
                       ("hold_step", "hold-drill"), ("browser_step", "browser-a11y"),
                       ("regression_step", "provider-regression-100"),
                       ("generic_step", "generic-rest-roundtrip")):
        got[name] = got["rows_by_name"].get(step, "NOT-FOUND")
    # How many assets the restore-fidelity leg actually compared. The count moves with the demo database,
    # so a face that quotes it has to be stamped rather than remembered: one round's "4 个资产字节一致"
    # was still on the checklist while the archive read 2.
    fidelity = str(got.get("restore", ""))
    match = re.search(r"(\d+) assets", fidelity)
    got["restore_assets"] = match.group(1) if match else "NOT-FOUND"
    report = browser_pair(run)
    got["browser_report"] = str(report.relative_to(ROOT)) if report else "MISSING"
    # The report-derived keys are declared up front so a run without a paired report reads NOT-FOUND rather
    # than losing the keys entirely: the cell table's orphan check compares one fixed key set against this
    # dict, and keys that appear and disappear with the evidence make that check answer a different question.
    for name in ("browser_views", "browser_scans", "browser_mobile_fit", "browser_hidden",
                 "browser_rendered_hidden", "browser_export_bytes", "browser_violations", "browser_commit",
                 "browser_uncaught", "browser_boot_clicks", "browser_report_dir", "console_lines", "console_app_lines", "console_network_lines",
                 "console_status_breakdown", "console_labels", "console_error_events", "refusal_lines",
                 "refusal_events", "refusal_endpoints", "refusal_403_events", "refusal_top",
                 "server_4xx_events", "server_endpoints", "server_request_lines"):
        got.setdefault(name, "NOT-FOUND")
    if report:
        data = json.loads(report.read_text(encoding="utf-8"))
        # `views_scanned` is what the scanner prints; the earlier guess `views` was never on the object, so
        # this cell read NOT-FOUND for a round while the faces kept quoting a view count copied by hand.
        got["browser_views"] = data.get("views_scanned", data.get("views", "NOT-FOUND"))
        got["browser_scans"] = data.get("axe_scans", data.get("scans", "NOT-FOUND"))
        got["browser_mobile_fit"] = data.get("mobile_fit_measured", "NOT-FOUND")
        got["browser_hidden"] = sum(int(s.get("hidden_marked", 0)) for s in data.get("scans", [])) \
            if data.get("scans") else "NOT-FOUND"
        got["browser_rendered_hidden"] = sum(len(s.get("hidden_but_rendered", [])) for s in data.get("scans", [])) \
            if data.get("scans") else "NOT-FOUND"
        got["browser_export_bytes"] = " / ".join(f"{e['bytes']}" for e in data.get("privacy_exports", [])) \
            or "NOT-FOUND"
        got["browser_boot_clicks"] = data.get("boot_window_clicks", "NOT-FOUND")
        got["browser_report_dir"] = report.parent.name
        got["browser_violations"] = data.get("violations_by_impact", "NOT-FOUND")
        got["browser_commit"] = data.get("git_commit", "NOT-FOUND")
        got["browser_uncaught"] = data.get("uncaught_errors", "NOT-FOUND")
        # The console lines are `sorted(set(...))` of `"<label>: <text>"`, so they are *lines*, not events:
        # two refusals under one label collapse. The count of raw events is not in the artifact at all.
        # Everything the record says about them has to be read off this shape, which is why the split is
        # reported by status, by label, and by whether the text is the network layer's own sentence.
        lines = data.get("console_errors", [])
        got["console_lines"] = len(lines)
        seen: dict[str, int] = {}
        labels: dict[str, set] = {}
        app_side = 0
        for line in lines:
            label, _, text = line.partition(":")
            # The label carries the viewport as its first field (`desktop-axe-studio`), and the prose
            # multiplies the remaining shape count by the two viewports. Counting the full label would
            # just reproduce the line count and hide whether a shape fired twice.
            shape = label.split("-", 1)[1] if "-" in label else label
            status = re.search(r"status of (\d{3})", text)
            if status:
                seen[status.group(1)] = seen.get(status.group(1), 0) + 1
                labels.setdefault(status.group(1), set()).add(shape)
            else:
                app_side += 1
        got["console_app_lines"] = app_side
        got["console_network_lines"] = len(lines) - app_side
        got["console_status_breakdown"] = " / ".join(f"{k} 记 {v} 行" for k, v in sorted(seen.items())) \
            or "NOT-FOUND"
        got["console_labels"] = "/".join(f"{k}×{len(v)}" for k, v in sorted(labels.items())) or "NOT-FOUND"
        # The request-side census the gate now records next to the console lines. Always emitted, even for
        # a report written before the field existed, so a cell that quotes it can refuse rather than invent.
        got["console_error_events"] = data.get("console_error_events", "NOT-FOUND")
        refusals = data.get("refusals")
        got["refusal_lines"] = len(refusals) if refusals is not None else "NOT-FOUND"
        got["refusal_events"] = data.get("refusal_events", "NOT-FOUND")
        # Per-endpoint counts come from `refusal_counts`; the deduplicated lines can only ever say "once".
        counts = data.get("refusal_counts")
        got["refusal_endpoints"] = len(counts) if counts else "NOT-FOUND"
        got["refusal_403_events"] = sum(n for tail, n in (counts or {}).items() if tail.endswith("-> 403"))
        if counts:
            top, hits = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
            got["refusal_top"] = f"{top} 共 {hits} 次"
        else:
            got["refusal_top"] = "NOT-FOUND"
        # The second observer: `server-refusals.txt` is written by scripts/server_refusal_census.py from
        # `docker compose logs api` over this run's own browser row window, so a face can quote what the
        # server answered next to what the pages saw, and the two can be reconciled by the resident test.
        census_file = report.parent / "server-refusals.txt"
        got["server_4xx_events"] = got["server_endpoints"] = got["server_request_lines"] = "NOT-FOUND"
        if census_file.exists():
            census_text = census_file.read_text(encoding="utf-8", errors="replace")
            for pattern, key in ((r"server_4xx_events: (\d+)", "server_4xx_events"),
                                 (r"request_lines_parsed: (\d+)", "server_request_lines")):
                found = re.search(pattern, census_text)
                if found:
                    got[key] = found.group(1)
            got["server_endpoints"] = len(re.findall(r"^\s*\d{3} \w+\s+\S+\s+\d+$", census_text, re.M))
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
