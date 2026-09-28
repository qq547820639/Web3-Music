#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数一遍服务端在同一时间窗里答出的 4xx，给浏览器侧的拒绝清单找一个独立观察者。

为什么需要它：《测试报告》第 22 步那段长期是手写的。它把报告里 `console_errors` 的**行数**当成
**拒绝次数**来写，又把三件不同的事归因到三行上——其中「移出对话框里邮箱写错」那一件根本没能
发出请求（属主侧的邮箱核对是前端拦的），而它占的那个标签实际压着两件不同的 403。要改这句话，
手上得有一件不来自门禁自己的读数：这一份来自 api 容器的访问日志，浏览器看不见它，它也看不见
浏览器 —— 两边对得上，那句「应用在拒绝的时候真的被问了」才站得住。

窗口不是猜的：它取自被认证那一轮 SUMMARY 里 `browser-a11y` 那一行自己写的起止时间戳，
所以 census 与门禁跑的是同一段时间。产物落在同一个证据目录里（`server-refusals.txt`），
《测试报告》引用的数字因此有一个可以对着看的东西，而不是一句只在终端里出现过一次的话。

用法：
    python3 scripts/server_refusal_census.py            # 最近一次全绿运行
    python3 scripts/server_refusal_census.py --write    # 把读数落成证据件
    python3 scripts/server_refusal_census.py --self-test
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# `docker compose logs --timestamps` prints `<service> | <RFC3339> INFO: <peer> - "<line>"`, while
# --no-log-prefix drops only the service part, so the pattern is anchored on the request line itself
# and searched rather than matched: a parser pinned to one shape silently reads "the server answered
# no refusals" on the other, which is the false green this whole script exists to avoid.
LOG_LINE = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\S*Z\s+INFO:\s+\S+ - "(\w+) (\S+) '
                      r'HTTP/[\d.]+" (\d{3})')
# The route the a11y gate fulfills itself (scripts/browser_a11y.py, walk_stale_panels). It must show up on
# the browser side and not on the server side: if it ever reaches the api, the "partial failure must not
# look like a crash" arm is exercising a real 500 rather than an injected one.
INJECTED = "/api/admin/v12/moderation"


def load_reader():
    spec = importlib.util.spec_from_file_location("srf_for_census", ROOT / "scripts/stamp_release_faces.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def target_run(stamp: str | None) -> dict:
    reader = load_reader()
    if stamp:
        # A chain calls this before its own evidence is staged, so the run it names may exist only on disk.
        tracked = {r["stamp"]: r for r in reader.runs()}
        if stamp in tracked:
            return tracked[stamp]
        return reader.run_from_disk(stamp)
    runs = [r for r in reader.runs() if r["fails"] == 0]
    if not runs:
        raise SystemExit("no tracked all-green acceptance run to take a window from")
    return max(runs, key=lambda r: r["stamp"])


def window(run: dict) -> tuple[str, str]:
    row = ROOT / run["dir"] / "SUMMARY.txt"
    text = row.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4 and parts[0] == "browser-a11y":
            return parts[-2], parts[-1]
    raise SystemExit(f"{run['stamp']}: SUMMARY has no browser-a11y row, so there is no window to census")


def compose_log(lo: str, hi: str) -> list[str]:
    cmd = ["docker", "compose", "logs", "api", "--timestamps", f"--since={lo}", f"--until={hi}"]
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if done.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} failed (rc={done.returncode}): {done.stderr.strip()[:200]}")
    return done.stdout.splitlines()


def count(lines: list[str]) -> tuple[collections.Counter, int]:
    """The 4xx census and how many request lines were parsed at all.

    Both are returned because "no refusals" and "nothing read" look identical in the first number alone.
    """
    out: collections.Counter = collections.Counter()
    parsed = 0
    for line in lines:
        m = LOG_LINE.search(line)
        if not m:
            continue
        parsed += 1
        _at, method, target, status = m.groups()
        code = int(status)
        if code < 400:
            continue
        out[(method, target.split("?")[0], code)] += 1
    return out, parsed


def browser_census(report: pathlib.Path) -> collections.Counter:
    """The gate's own per-endpoint counts, as endpoint tuples (the label prefix is not part of a tuple).

    `refusal_counts` is the field that carries them; a report written before it existed has only the
    deduplicated lines, where every endpoint reads exactly once -- which would make the count comparison
    below a false red rather than a finding, so the fallback is explicit about being degraded.
    """
    data = json.loads(report.read_text(encoding="utf-8"))
    out: collections.Counter = collections.Counter()
    for tail, n in (data.get("refusal_counts") or {}).items():
        m = re.match(r"^(\w+) (\S+) -> (\d{3})$", tail)
        if m:
            method, path, status = m.groups()
            out[(method, path, int(status))] += int(n)
    return out


def surplus_times(lines: list[str], server: collections.Counter,
                  seen: collections.Counter) -> dict[tuple, list[str]]:
    """For every tuple the server answered more often than the gate recorded, the server's own timestamps.

    A red that says "27 vs 26" cannot be triaged: the timestamps are what tell a response delivered after
    the page closed apart from a page whose traffic the hook never saw at all.
    """
    out: dict[tuple, list[str]] = {}
    for key in server:
        extra = server[key] - seen.get(key, 0)
        if extra <= 0:
            continue
        stamps = [m.group(1) for m in (LOG_LINE.search(line) for line in lines)
                  if m and (m.group(2), m.group(3).split("?")[0], int(m.group(4))) == key]
        out[key] = stamps[-extra:]
    return out


def report_span(report: pathlib.Path) -> tuple[str, str]:
    """First and last refusal the gate recorded, or its start/end stamps when it recorded none."""
    data = json.loads(report.read_text(encoding="utf-8"))
    stamps = sorted(e.get("ts", "") for e in data.get("refusal_timeline") or [])
    return (stamps[0] if stamps else str(data.get("gate_started_at", "?")),
            stamps[-1] if stamps else str(data.get("generated_at", "?")))


def degraded(report: pathlib.Path) -> bool:
    return "refusal_counts" not in json.loads(report.read_text(encoding="utf-8"))


def reconcile(server: collections.Counter, seen: collections.Counter, parsed: int, raw: int) -> list[str]:
    """Server ⊆ browser, with one required exception: what the gate fulfills never reaches the server.

    Counts are compared as data, not as rendered strings. A page can see a refusal the server never
    answered (an injected one), but the reverse would mean the gate missed traffic it was sitting on --
    which is precisely the blind spot this census exists to close.
    """
    problems = []
    if parsed == 0:
        return [f"the window held {raw} log line(s) and none parsed as a request line, so a zero here "
                "means the parser saw nothing, not that the server answered no refusals"]
    for tuple_, n in sorted(server.items()):
        if seen.get(tuple_, 0) < n:
            problems.append(f"the server answered {n}x {tuple_[0]} {tuple_[1]} -> {tuple_[2]} but the "
                            f"gate recorded {seen.get(tuple_, 0)}")
    for tuple_ in sorted(seen):
        if tuple_[1] == INJECTED and server.get(tuple_, 0):
            problems.append(f"the injected route {INJECTED} reached the api, so the stale-panel arm is "
                            "reading a real failure rather than an injected one")
    return problems


def between(counter: collections.Counter, low: int, high: int) -> int:
    """Sum of one observer's events whose status falls in [low, high).

    The two summary lines are named for the class they compare, so a 5xx on either side cannot inflate a
    figure the release record quotes as "次 4xx" -- which matters most for the gate side, where the injected
    stale-panel route is a 500 by design and never reaches the api at all.
    """
    return sum(n for (method, path, code), n in counter.items() if low <= code < high)


def render(run: dict, lo: str, hi: str, server: collections.Counter, seen: collections.Counter,
           parsed: int, raw: int, provenance: str = "") -> str:
    head = [
        "# generated by scripts/server_refusal_census.py",
        f"# evidence source: {run['stamp']} (commit {str(run['commit'])[:7]})",
        provenance or "# window = the browser-a11y row's own stamps in SUMMARY.txt",
        f"window: {lo} -> {hi}",
        f"command: docker compose logs api --timestamps --since={lo} --until={hi}",
        f"log_lines_read: {raw}",
        f"request_lines_parsed: {parsed}",
        f"server_4xx_events: {between(server, 400, 500)}",
        f"server_5xx_events: {between(server, 500, 600)}",
        f"gate_4xx_events: {between(seen, 400, 500)}",
        f"gate_5xx_events: {between(seen, 500, 600)}",
        "STATUS METHOD PATH COUNT",
    ]
    rows = [f"{code:>5} {method:<6} {path} {n}" for (method, path, code), n in sorted(server.items())]
    return "\n".join(head + rows) + "\n"


def self_test() -> int:
    server = collections.Counter({("GET", "/api/auth/me", 401): 28, ("POST", "/api/account/erasure", 403): 2})
    seen = collections.Counter({("GET", "/api/auth/me", 401): 28, ("POST", "/api/account/erasure", 403): 2,
                                 ("GET", INJECTED, 500): 2})
    arms = []
    arms.append(("the honest pair reports nothing", reconcile(server, seen, 40, 60) == []))
    missing = dict(seen)
    del missing[("GET", "/api/auth/me", 401)]
    arms.append(("a refusal the gate missed is named",
                 any("the gate recorded 0" in p for p in reconcile(server, collections.Counter(missing), 40, 60))))
    short = collections.Counter(seen)
    short[("GET", "/api/auth/me", 401)] = 27
    arms.append(("one event short on the gate side is named",
                 any("28x GET /api/auth/me" in p for p in reconcile(server, short, 40, 60))))
    reached = collections.Counter(server)
    reached[("GET", INJECTED, 500)] += 1
    arms.append(("an injected route that reached the api is named",
                 any("reached the api" in p for p in reconcile(reached, seen, 40, 60))))
    arms.append(("lines that do not parse are refused, not read as zero",
                 any("the parser saw nothing" in p for p in reconcile(collections.Counter(),
                                                                      collections.Counter(), 0, 60))))
    arms.append(("an empty window is the same refusal",
                 bool(reconcile(collections.Counter(), collections.Counter(), 0, 0))))
    sample = count(['api-1  | 2026-09-28T08:56:01.353095727Z INFO:     172.28.95.1:42782 - '
                    '"GET /api/auth/me HTTP/1.1" 401 Unauthorized',
                    '2026-09-28T08:56:01Z INFO:     10.0.0.1:1 - "POST /api/account/erasure HTTP/1.1" 403',
                    'api-1  | 2026-09-28T08:56:01.4Z INFO:     10.0.0.1:2 - "GET /api/x HTTP/1.1" 200 OK',
                    'not a log line at all'])
    arms.append((f"both docker line shapes parse (read {sample[1]} of 4 lines, {sum(sample[0].values())} "
                 f"refusals)", sample[1] == 3 and sample[0][("GET", "/api/auth/me", 401)] == 1
                 and sample[0][("POST", "/api/account/erasure", 403)] == 1))
    surplus_lines = [f'api-1  | {t}Z INFO:     172.28.0.9:1 - "GET /api/auth/me HTTP/1.1" 401 Unauthorized'
                     for t in ("2026-01-01T00:00:01", "2026-01-01T00:00:02", "2026-01-01T00:00:03")]
    three, two = (collections.Counter({("GET", "/api/auth/me", 401): n}) for n in (3, 2))
    arms.append(("the surplus names the timestamp the gate missed",
                 surplus_times(surplus_lines, three, two).get(("GET", "/api/auth/me", 401))
                 == ["2026-01-01T00:00:03"]))
    arms.append(("an even pair produces no surplus timestamps",
                 surplus_times(surplus_lines[:2], two, two) == {}))
    fake_run = {"stamp": "acceptance-selftest", "commit": "0" * 40}
    text = render(fake_run, "a", "b", server, seen, 40, 60)
    arms.append(("the two 4xx lines compare the same class, with the injected 500 on its own line",
                 "server_4xx_events: 30" in text and "gate_4xx_events: 30" in text
                 and "server_5xx_events: 0" in text and "gate_5xx_events: 2" in text))
    leaked = collections.Counter(server)
    leaked[("GET", INJECTED, 500)] = 1
    text_real = render(fake_run, "a", "b", leaked, seen, 40, 60)
    arms.append(("a real 500 in the window cannot inflate the figure the faces quote as 4xx",
                 "server_4xx_events: 30" in text_real and "server_5xx_events: 1" in text_real))
    width = max(len(name) for name, _ in arms)
    bad = 0
    for name, ok in arms:
        print(f"  {'OK  ' if ok else 'FAIL'} {name:<{width}}")
        bad += 0 if ok else 1
    print(f"self-test: {len(arms)} arms, failures: {'none' if not bad else bad}")
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="stamp of a tracked (or on-disk) acceptance run; default newest green")
    ap.add_argument("--since", help="explicit window start, for hosts that have no acceptance SUMMARY")
    ap.add_argument("--until", help="explicit window end, paired with --since")
    ap.add_argument("--write", action="store_true", help="write the artifact into the browser evidence dir")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if bool(args.since) != bool(args.until):
        raise SystemExit("--since and --until go together")

    reader = load_reader()
    if args.since:
        # CI dispatches the legs one at a time and has no SUMMARY to take a row window from, so the workflow
        # hands over the clock it measured around its own browser run and the newest report on disk is the
        # one that was just written.
        reports = sorted((reader.EVIDENCE).glob("browser-a11y-*/report.json"))
        if not reports:
            raise SystemExit(f"no browser report under {reader.EVIDENCE} to reconcile against")
        report = reports[-1]
        run = {"stamp": report.parent.name,
               "commit": json.loads(report.read_text(encoding="utf-8")).get("git_commit", "?")}
        provenance = "# window = the caller's own clock around its browser run (--since/--until)"
        lo, hi = args.since, args.until
    else:
        run = target_run(args.run)
        report = reader.browser_pair(run)
        if report is None:
            raise SystemExit(f"{run['stamp']}: no browser report pairs to this run by commit and window")
        provenance = "# window = the browser-a11y row's own stamps in SUMMARY.txt"
        lo, hi = window(run)
    lines = compose_log(lo, hi)
    server, parsed = count(lines)
    seen = browser_census(report)
    problems = reconcile(server, seen, parsed, len(lines))
    if degraded(report):
        problems.insert(0, f"{report.parent.name}/report.json has no `refusal_counts`, so the gate's side "
                           "of the comparison is empty by construction -- re-run the browser leg to get a "
                           "report that records per-endpoint counts")
    if problems:
        # Name the when, not just the how-many, so a red of this kind is triageable from the log alone.
        first, last = report_span(report)
        for key, when in sorted(surplus_times(lines, server, seen).items()):
            print(f"# surplus server event(s) for {key[0]} {key[1]} -> {key[2]} at {', '.join(when)}; "
                  f"the gate's own refusals run {first} .. {last}", file=sys.stderr)
    text = render(run, lo, hi, server, seen, parsed, len(lines), provenance)
    print(text, end="")
    print(f"artifact would go to: {report.parent / 'server-refusals.txt'}")
    for problem in problems:
        print(f"::error title=refusal-census::{problem}", file=sys.stderr)
    if args.write:
        if problems:
            raise SystemExit("refusing to write an artifact whose two observers disagree")
        (report.parent / "server-refusals.txt").write_text(text, encoding="utf-8")
        print(f"wrote {report.parent / 'server-refusals.txt'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
