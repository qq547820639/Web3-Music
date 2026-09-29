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
import datetime
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
LOG_LINE = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z?\s+INFO:\s+\S+ - "(\w+) (\S+) '
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


def count(lines: list[str]) -> tuple[collections.Counter, int, collections.Counter]:
    """The 4xx census, how many request lines were parsed at all, and every request line per endpoint.

    All three are returned because "no refusals" and "nothing read" look identical in the first number
    alone, and because the second one is the only denominator the request axis can be judged against: the
    gate counts what it put on the wire whether or not an answer came back, so a 4xx the gate did not
    record is only a blind spot if the api logged a request the gate never issued.
    """
    out: collections.Counter = collections.Counter()
    every: collections.Counter = collections.Counter()
    parsed = 0
    for line in lines:
        m = LOG_LINE.search(line)
        if not m:
            continue
        parsed += 1
        _at, method, target, status = m.groups()
        every[(method, target.split("?")[0])] += 1
        code = int(status)
        if code < 400:
            continue
        out[(method, target.split("?")[0], code)] += 1
    return out, parsed, every


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


def browser_requests(report: pathlib.Path) -> collections.Counter:
    """What the auditor counted putting on the wire, keyed the way the api log keys it.

    This is the witness that answers *before* any response, so unlike the refusal axis it cannot be
    blinded by a page that was reloaded or closed while a request was in flight.
    """
    out: collections.Counter = collections.Counter()
    for tail, n in (json.loads(report.read_text(encoding="utf-8")).get("request_counts") or {}).items():
        m = re.match(r"^(\w+) (\S+)$", tail)
        if m:
            out[(m.group(1), m.group(2))] += int(n)
    return out


def has_request_axis(report: pathlib.Path) -> bool:
    """Whether the report's own gate recorded this axis at all.

    A report written before it existed has no reading, which is not the same as a reading of zero: judging
    the axis there would turn every certified run on record into a false red.
    """
    return "request_counts" in json.loads(report.read_text(encoding="utf-8"))


def unsettled_at_close(report: pathlib.Path) -> list[str] | None:
    """The gate's own note about pages it closed while requests were still in flight.

    None means the report predates the field -- no reading, which is not the same as an empty list, and the
    caller keeps the shortfall red rather than excusing it with an absence.
    """
    return json.loads(report.read_text(encoding="utf-8")).get("unsettled_at_close")


def outstanding_keys(unsettled: list) -> set[str]:
    """The endpoints a closing page was still waiting on, as "METHOD path" keys.

    Entries are shaped `{"label", "error", "outstanding": [...]}`. An older string entry, or one that came
    back without the reading, contributes nothing -- and contributing nothing is what keeps the shortfall
    red rather than excused on a page's bare word.
    """
    keys: set[str] = set()
    for entry in unsettled or []:
        if isinstance(entry, dict):
            keys.update(k for k in (entry.get("outstanding") or []) if isinstance(k, str) and k != "NO-READING")
    return keys


def aborted_events(report: pathlib.Path) -> list[str]:
    """The gate's no-answer requests, each with the document that issued it.

    The document is carried because it is the field that settled the 2026-09-28 label question (port 4174
    was the Control Plane's page, not a studio popup), and it is the only attribution the gate can print
    without arguing about clocks -- the two observers' stamps disagree by more than a hundred milliseconds
    within a single run.
    """
    out = []
    for e in (json.loads(report.read_text(encoding="utf-8")).get("abort_timeline") or []):
        document = e.get("document")
        out.append(f"{e.get('label', '?')}: {e.get('event', '?')}"
                   + (f" [document={document}]" if document else ""))
    return out


def request_axis(server: collections.Counter, server_all: collections.Counter,
                 issued: collections.Counter) -> list[str]:
    """Every endpoint that refused at least once must have been issued by the auditor as often as the api logged it.

    Judged only on refusing endpoints: the same window holds the health check's `GET /ready` and
    Prometheus' `GET /metrics`, which no page issues and the gate was never meant to account for. A
    shortfall here is the other half of a one-event disagreement: it says something outside the armed
    pages -- and outside this process's own `api_post` calls -- talked to the api.
    """
    refusing = {(method, path) for (method, path, _code) in server}
    problems = []
    for (method, path), n in sorted(server_all.items()):
        if (method, path) not in refusing or path == INJECTED:
            continue
        if issued.get((method, path), 0) < n:
            problems.append(f"the api logged {n} request line(s) for {method} {path} while the auditor "
                            f"counted {issued.get((method, path), 0)} it issued, so traffic the gate never "
                            "saw reached the api from outside its armed pages")
    return problems


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


def shortfall(server: collections.Counter, seen: collections.Counter) -> list[tuple]:
    """The tuples the api answered more often than the gate recorded, as (tuple, server, gate) triples."""
    return [(t, n, seen.get(t, 0)) for t, n in sorted(server.items()) if seen.get(t, 0) < n]


TAIL_TOLERANCE_S = 0.5


def _stamp(text: str):
    """A docker/report timestamp as a comparable datetime, or None when it does not parse.

    Both shapes exist in the wild: the api logs nine fractional digits, the report's own stamps carry
    three or none, and every one of them ends in Z.
    """
    body = text.strip()
    if body.endswith("Z"):
        body = body[:-1]
    head, _, frac = body.partition(".")
    try:
        moment = datetime.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    if frac:
        moment = moment.replace(microsecond=int((frac + "000000")[:6]))
    return moment


def observation_horizon(report: pathlib.Path) -> str | None:
    """The instant the gate stopped being able to record a refusal, as the report itself states it."""
    return json.loads(report.read_text(encoding="utf-8")).get("gate_last_refusal_at")


def tail_of(short_triples: list, surplus: dict, horizon: str | None) -> tuple[list, list]:
    """Split a shortfall into events the gate could still have seen and events it provably could not.

    After `gate_last_refusal_at` the gate's pages are gone: an answer delivered there has no observer on
    the browser side, which is the same shape the `unsettled` reading excuses -- except this one is proven
    by a timestamp the report itself carries, so it does not depend on a page having confessed to being
    unsettled. Measured across the sixteen tracked census artifacts the window outlives the gate's last
    observation by 2 to 9 seconds (median 6), and the live demonstration on `browser-a11y-20260929T120005Z`
    reproduced the historical signature exactly: one refusal answered in that tail read "the server
    answered 29x GET /api/auth/me -> 401 but the gate recorded 28" with nothing else changed.

    The tolerance is not slack, it is a measured quantity: the same event's two stamps sat -32..+143 ms
    apart across one run (noted where `surplus_times` prints them), so anything within half a second of
    the horizon counts as inside. An unparsable stamp counts as inside too, and so does everything when
    the report predates `gate_last_refusal_at` -- the conservative direction keeps a shortfall judged
    rather than excused.
    """
    limit = None
    if horizon:
        limit = _stamp(horizon)
        if limit is not None:
            limit += datetime.timedelta(seconds=TAIL_TOLERANCE_S)
    judged, tail = [], []
    for tuple_, n, got in short_triples:
        stamps = surplus.get(tuple_) or []
        parsed = [_stamp(s) for s in stamps]
        if limit is None or not parsed or any(m is None or m <= limit for m in parsed):
            judged.append((tuple_, n, got))
        else:
            tail.append((tuple_, n, got, stamps))
    return judged, tail


def reconcile(server: collections.Counter, seen: collections.Counter, parsed: int, raw: int,
              unsettled: list | None = None, excuse_tail: frozenset = frozenset()) -> list[str]:
    """Server ⊆ browser, with one required exception: what the gate fulfills never reaches the server.

    Counts are compared as data, not as rendered strings. A page can see a refusal the server never
    answered (an injected one), but the reverse would mean the gate missed traffic it was sitting on --
    which is precisely the blind spot this census exists to close.

    `unsettled` is what the gate itself recorded about its own teardown: pages that had not gone
    network-idle when the walk closed them. A shortfall with such a page named is the browser's answer
    being logged after the page was already gone, which no page-side observer could record; it is reported
    by the caller instead of judged. An empty list, or no reading at all, keeps the shortfall red -- the
    exemption is only available when the run itself proves the window was open, and the gate now settles
    its pages before closing them so that this is the exception rather than the routine.
    """
    problems = []
    if parsed == 0:
        return [f"the window held {raw} log line(s) and none parsed as a request line, so a zero here "
                "means the parser saw nothing, not that the server answered no refusals"]
    waiting = outstanding_keys(unsettled)
    for tuple_, n, got in shortfall(server, seen):
        if tuple_ in excuse_tail:
            continue
        if f"{tuple_[0]} {tuple_[1]}" in waiting:
            # Named and pointable: some page was still waiting on exactly this endpoint when the gate
            # closed it, so the api's answer had no page left to hand it to. Any other shortfall is a
            # shortfall the gate simply did not record, and stays red with a note on its side.
            continue
        problems.append(f"the server answered {n}x {tuple_[0]} {tuple_[1]} -> {tuple_[2]} but the "
                        f"gate recorded {got}")
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
           parsed: int, raw: int, provenance: str = "", issued: collections.Counter | None = None,
           aborted: int | None = None) -> str:
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
    ]
    # Written only when the report carried the axis: an absent reading stays absent rather than becoming a
    # zero a later reader would quote as a measurement.
    if issued is not None:
        head.append(f"gate_issued_events: {sum(issued.values())}")
    if aborted is not None:
        head.append(f"gate_aborted_events: {aborted}")
    head.append("STATUS METHOD PATH COUNT")
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
    arms.append((f"the 200 is counted too, because the request axis needs every line ({dict(sample[2])})",
                 sample[2][("GET", "/api/auth/me")] == 1 and sample[2][("GET", "/api/x")] == 1
                 and sample[2][("POST", "/api/account/erasure")] == 1))
    surplus_lines = [f'api-1  | {t}Z INFO:     172.28.0.9:1 - "GET /api/auth/me HTTP/1.1" 401 Unauthorized'
                     for t in ("2026-01-01T00:00:01", "2026-01-01T00:00:02", "2026-01-01T00:00:03")]
    three, two = (collections.Counter({("GET", "/api/auth/me", 401): n}) for n in (3, 2))
    arms.append(("the surplus names the timestamp the gate missed",
                 surplus_times(surplus_lines, three, two).get(("GET", "/api/auth/me", 401))
                 == ["2026-01-01T00:00:03"]))
    arms.append(("an even pair produces no surplus timestamps",
                 surplus_times(surplus_lines[:2], two, two) == {}))
    subsecond = [f'api-1  | 2026-01-01T00:00:{frac}.123456789Z INFO:     172.28.0.9:1 - '
                 f'"GET /api/auth/me HTTP/1.1" 401 Unauthorized' for frac in ("01", "01", "01")]
    sub, gate2 = (collections.Counter({("GET", "/api/auth/me", 401): 3}),
                  collections.Counter({("GET", "/api/auth/me", 401): 2}))
    stamps = surplus_times(subsecond, sub, gate2)[("GET", "/api/auth/me", 401)]
    arms.append(("sub-second stamps survive the parser, so same-second events stay countable",
                 all(s.startswith("2026-01-01T00:00:01.") for s in stamps) and len(stamps) == 1))
    one_short_server = collections.Counter({("GET", "/api/auth/me", 401): 3})
    two_seen = collections.Counter({("GET", "/api/auth/me", 401): 2})
    arms.append(("a shortfall the gate cannot explain stays red",
                 any("2x" in p or "3x" in p for p in reconcile(one_short_server, two_seen, 40, 60, []))))
    arms.append(("a shortfall with no reading on the gate's side stays red too",
                 bool(reconcile(one_short_server, two_seen, 40, 60, None))))
    waiting_entry = [{"label": "desktop-stale-admin", "error": "TimeoutError",
                      "outstanding": ["GET /api/auth/me"]}]
    arms.append(("a shortfall the gate named a page still waiting on THAT endpoint for is excused",
                 reconcile(one_short_server, two_seen, 40, 60, waiting_entry) == []
                 and bool(reconcile(one_short_server, two_seen, 40, 60, []))))
    off_target = [{"label": "desktop-axe-studio", "error": "TimeoutError",
                   "outstanding": ["POST /api/auth/logout"]}]
    legacy_shape = [{"label": "old-shape", "error": "TimeoutError"}]
    arms.append(("a named page waiting on some OTHER endpoint does not excuse the shortfall, and neither "
                 "does an entry written in the old string shape",
                 bool(reconcile(one_short_server, two_seen, 40, 60, off_target))
                 and bool(reconcile(one_short_server, two_seen, 40, 60, legacy_shape))))
    arms.append(("the outstanding keys are read as data, not as a string blob",
                 outstanding_keys(waiting_entry) == {"GET /api/auth/me"}
                 and outstanding_keys([{"label": "x", "outstanding": ["NO-READING"]}]) == set()
                 and outstanding_keys(["legacy string entry"]) == set()))
    arms.append(("the shortfall list is what the note prints, as data",
                 shortfall(one_short_server, two_seen) == [(("GET", "/api/auth/me", 401), 3, 2)]))
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
    # The request axis: the witness taken before any answer, which is what lets a one-event disagreement be
    # attributed instead of argued about. Each arm is the polarity that would otherwise read as green.
    refused = collections.Counter({("GET", "/api/auth/me", 401): 2})
    logged = collections.Counter({("GET", "/api/auth/me"): 5, ("GET", "/ready"): 9})
    issued = collections.Counter({("GET", "/api/auth/me"): 5})
    arms.append(("a fully accounted endpoint raises nothing on the request axis",
                 request_axis(refused, logged, issued) == []))
    arms.append(("one request line the gate never issued is named, with both counts",
                 any("logged 6 request line(s) for GET /api/auth/me while the auditor counted 5" in p
                     for p in request_axis(refused, collections.Counter({("GET", "/api/auth/me"): 6}),
                                           issued))))
    arms.append(("the health check's lines are not the gate's to account for",
                 request_axis(refused, collections.Counter({("GET", "/ready"): 9}),
                              collections.Counter()) == []))
    arms.append(("the endpoint the gate fulfills itself is exempt on this axis too",
                 request_axis(refused, collections.Counter({("GET", INJECTED): 1}),
                              collections.Counter()) == []))
    empty_issued = collections.Counter()
    arms.append(("an endpoint the gate issued nothing for is still named, not skipped",
                 any("counted 0 it issued" in p for p in
                     request_axis(refused, collections.Counter({("GET", "/api/auth/me"): 3}), empty_issued))))
    axis_text = render(fake_run, "a", "b", server, seen, 40, 60, issued=logged, aborted=2)
    arms.append(("the axis is written when the report carried it and left out when it did not",
                 "gate_issued_events: 14" in axis_text and "gate_aborted_events: 2" in axis_text
                 and "gate_issued_events" not in text and "gate_aborted_events" not in text))
    # The observation horizon: a shortfall the gate provably could not see is reported, not judged.
    late = "2026-09-28T08:56:30.100Z"
    short_one = collections.Counter(seen)
    short_one[("GET", "/api/auth/me", 401)] = 27
    late_only = {("GET", "/api/auth/me", 401): ["2026-09-28T08:56:31.500Z"]}
    judged, tail = tail_of(shortfall(server, short_one), late_only, late)
    arms.append(("a shortfall whose every stamp is after the gate's own horizon is tail, not red",
                 judged == [] and len(tail) == 1))
    mixed = {("GET", "/api/auth/me", 401): ["2026-09-28T08:56:20.000Z", "2026-09-28T08:56:29.900Z"]}
    judged, tail = tail_of(shortfall(server, short_one), mixed, late)
    arms.append(("one stamp inside the horizon keeps the shortfall judged", len(judged) == 1 and tail == []))
    judged, tail = tail_of(shortfall(server, short_one), late_only, None)
    arms.append(("a report without the horizon keeps everything judged", len(judged) == 1 and tail == []))
    judged, tail = tail_of(shortfall(server, short_one),
                           {("GET", "/api/auth/me", 401): ["not-a-stamp"]}, late)
    arms.append(("an unparsable stamp is inside, not excused", len(judged) == 1 and tail == []))
    nanos = {("GET", "/api/auth/me", 401): ["2026-09-28T08:56:30.600123456Z"]}
    judged, tail = tail_of(shortfall(server, short_one), nanos, late)
    arms.append(("an event half a second past the horizon is still tail", judged == [] and len(tail) == 1))
    arms.append(("reconcile honours the named tail set",
                 reconcile(server, short_one, 40, 60,
                           excuse_tail=frozenset({("GET", "/api/auth/me", 401)})) == []))
    arms.append(("and the excuse is named: without it the same pair is red",
                 any("28x GET /api/auth/me" in p for p in reconcile(server, short_one, 40, 60))))
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
    server, parsed, every = count(lines)
    seen = browser_census(report)
    unsettled = unsettled_at_close(report)
    surplus = surplus_times(lines, server, seen)
    horizon = observation_horizon(report)
    judged_short, tail = tail_of(shortfall(server, seen), surplus, horizon)
    problems = reconcile(server, seen, parsed, len(lines), unsettled,
                         excuse_tail=frozenset(t for t, _, _, _ in tail))
    if degraded(report):
        problems.insert(0, f"{report.parent.name}/report.json has no `refusal_counts`, so the gate's side "
                           "of the comparison is empty by construction -- re-run the browser leg to get a "
                           "report that records per-endpoint counts")
    # The request axis is the attribution: it was taken before any answer came back, so it can say whether a
    # refusal the gate did not record is a response the page never got or traffic from a session the gate
    # never armed. A report that predates the axis has no reading, which is reported as such rather than
    # judged as a violation.
    issued = browser_requests(report) if has_request_axis(report) else None
    if issued is not None:
        problems += request_axis(server, every, issued)
    aborted = aborted_events(report)
    short = judged_short
    waiting = outstanding_keys(unsettled)
    excused = [f"{n - got}x {m} {pth} -> {code}" for (m, pth, code), n, got in short
               if f"{m} {pth}" in waiting]
    if excused:
        named = " | ".join(f"{e.get('label', '?')} waiting on {', '.join(e.get('outstanding') or [])}"
                           for e in (unsettled or []) if isinstance(e, dict))
        print("# excused on the gate's own teardown reading (" + ", ".join(excused) + "): a page was closed "
              "while still waiting on that endpoint, so the api had no page left to answer -- " + named,
              file=sys.stderr)
    if problems:
        if issued is None:
            print("# request axis: the paired report has no `request_counts`, so nothing here can say "
                  "whether the gate issued the traffic the api logged -- an absent reading, not a red",
                  file=sys.stderr)
        for line in aborted:
            print(f"# the gate recorded a request that got no answer back: {line}", file=sys.stderr)
        first, last = report_span(report)
        for key, when in sorted(surplus.items()):
            # The newest N stamps of the disputed tuple, not an attribution: which of them went unmatched is
            # what the request axis answers, and clock order alone cannot say it on this host (the two
            # observers' stamps disagree by -32..+143 ms across one run, measured 2026-09-28).
            print(f"# {key[0]} {key[1]} -> {key[2]}: the server answered it more often than the gate "
                  f"recorded; its own stamps for that tuple end at {', '.join(when)}, and the gate's "
                  f"refusals run {first} .. {last}", file=sys.stderr)
    text = render(run, lo, hi, server, seen, parsed, len(lines), provenance, issued=issued,
                  aborted=len(aborted) if issued is not None else None)
    for tuple_, n, got, stamps in tail:
        note = (f"# tail beyond the gate's own horizon: the server answered {n - got}x "
                f"{tuple_[0]} {tuple_[1]} -> {tuple_[2]} at {', '.join(stamps)}, after its last "
                f"recorded refusal {horizon} -- no page was left to hand the answer to; reported, "
                "not judged")
        print(note, file=sys.stderr)
        text += note + "\n"
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
