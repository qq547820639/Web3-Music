"""Cell substitution for the release-record faces -- see the module docstring in stamp_release_faces.py.

Kept separate from the reader so each face's cell table stays readable. A cell is (file, a marker that must
pick exactly one line, a regex that must match exactly once on that line, and a template built only from
readings the tool actually obtained). Any miss aborts the whole write: a half-stamped round is worse than a
refused one, and a `?` in a release record is a fabricated reading.

Why an executor at all: the table sat without one for a round, and nothing noticed that three of its cells
pinned the short commit as `([0-9a-f]7)` -- one hex digit plus a literal `7`, which matches almost no sha.
A pattern nobody runs cannot fail, so `--check` resolves every cell against the real faces and reports the
count, and the resident test (`tests/unit/test_release_face_cells.py`) does the same on every ladder run.

    python scripts/release_face_cells.py --check                      # resolve only, read the census
    python scripts/release_face_cells.py --apply --figures f.json     # gate, then write, then re-read
    python scripts/release_face_cells.py --self-test                  # the engine's own arms

Values arrive as a JSON object (stamp_release_faces.py --json produces it, and it adds the derived figures by
importing `derive` here). The aggregate figures -- prior-green count, commit sequence, per-run row widths,
judged-red total and per-day split -- are computed by `derive`, which imports the *guard's own* module
(`tests/unit/test_release_record_consistency.py`) and calls its `archived_runs`/`split`/`days`. That is on
purpose: a second copy of the rule would drift exactly the way the sentence it stamps used to.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import re
import string
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Template keys come from figures(): stamp, commit, rows, pass, skipped, fails, started, ended, host_load,
# cpus, disk, fresh, unit_passed, tracked, listed, routes, matrix_writes, mfa, member, erasure,
# media_scan, report, hold, reconcile, lease, restore, regression, regression_ms, generic, generic_ms,
# plus derive(): short, green_count, green_list_cn, green_list_arrow, green_list_en, green_rows,
# narrowest_rows, widest_rows, repeat_width, repeat_times, red_count, red_days_cn, red_days_en, run_date,
# unit_files, mfa_step, media_scan_step, rows_now.
CELLS = [
    # ---------------------------------------------------------------- docs/RELEASE_CHECKLIST.md, the status line
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"release-evidence/acceptance-(\d{8}T\d{6}Z)/", "release-evidence/acceptance-{stamp}/"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"commit `([0-9a-f]{7})`", "commit `{short}`"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z → \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z，证据",
     "{started} → {ended}，证据"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"\*\*(\d+) 步全 PASS \+ (\d+) 步按开关跳过（共 (\d+) 行）\*\*",
     "**{pass} 步全 PASS + {skipped} 步按开关跳过（共 {rows} 行）**"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"host_load=\"([^\"]*)\"", 'host_load="{host_load}"'),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"Docker VM (\d+) vCPU", "Docker VM {cpus} vCPU"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"单元测试 (\d+) 条（该步跑", "单元测试 {unit_passed} 条（该步跑"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"末行打印 `(\d+) passed`", "末行打印 `{unit_passed} passed`"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"tracked=(\d+) listed=(\d+)", "tracked={tracked} listed={listed}"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"`authority matrix agrees with the code: (\d+) routes, (\d+) writes`",
     "`authority matrix agrees with the code: {routes} routes, {matrix_writes} writes`"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"同一主机已有 (\d+) 次 `FAIL=0`", "同一主机已有 {green_count} 次 `FAIL=0`"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"次 `FAIL=0`（`[0-9a-f]{7}`(?:, `[0-9a-f]{7}`)*，旧→新", "次 `FAIL=0`（{green_list_cn}，旧→新"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"当时分别是 ([\d/]+) 行", "当时分别是 {green_rows} 行"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"同样 (\d+) 行绿了 (\d+) 次", "同样 {repeat_width} 行绿了 {repeat_times} 次"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"留档的判红共 (\d+) 份（[^）]*）", "留档的判红共 {red_count} 份（{red_days_cn}）"),
    ("docs/RELEASE_CHECKLIST.md", "media scan drill:",
     r"media scan drill: (\d+/\d+)", "media scan drill: {media_scan}"),
    # ---------------------------------------------------------------- docs/TEST_REPORT.md
    ("docs/TEST_REPORT.md", "权威运行（",
     r"权威运行（(\d{4}-\d{2}-\d{2})）", "权威运行（{run_date}）"),
    ("docs/TEST_REPORT.md", "权威运行（",
     r"\*\*(\d+) 步 PASS \+ (\d+) 步按开关跳过（共 (\d+) 行）\*\*",
     "**{pass} 步 PASS + {skipped} 步按开关跳过（共 {rows} 行）**"),
    ("docs/TEST_REPORT.md", "权威运行（", r"commit `([0-9a-f]{7})`", "commit `{short}`"),
    ("docs/TEST_REPORT.md", "权威运行（",
     r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z → \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z，逐步日志",
     "{started} → {ended}，逐步日志"),
    ("docs/TEST_REPORT.md", "权威运行（",
     r"compose 日志见 `release-evidence/acceptance-(\d{8}T\d{6}Z)/`",
     "compose 日志见 `release-evidence/acceptance-{stamp}/`"),
    ("docs/TEST_REPORT.md", "权威运行（", r'host_load="([^"]*)"', 'host_load="{host_load}"'),
    ("docs/TEST_REPORT.md", "权威运行（", r"Docker VM (\d+) vCPU", "Docker VM {cpus} vCPU"),
    ("docs/TEST_REPORT.md", "权威运行（", r"已有 (\d+) 次 `FAIL=0`", "已有 {green_count} 次 `FAIL=0`"),
    ("docs/TEST_REPORT.md", "权威运行（",
     r"次 `FAIL=0`（`[0-9a-f]{7}`(?:, `[0-9a-f]{7}`)*，旧→新", "次 `FAIL=0`（{green_list_cn}，旧→新"),
    ("docs/TEST_REPORT.md", "权威运行（", r"当时分别 ([\d/]+) 行", "当时分别 {green_rows} 行"),
    ("docs/TEST_REPORT.md", "权威运行（", r"同样 (\d+) 行绿了 (\d+) 次", "同样 {repeat_width} 行绿了 {repeat_times} 次"),
    ("docs/TEST_REPORT.md", "发现记录同样保留", r"留档判红共 (\d+) 份（[^）]*）",
     "留档判红共 {red_count} 份（{red_days_cn}）"),
    ("docs/TEST_REPORT.md", "第二因子演练", r"\*\*(\d+/\d+)\*\*（流水线第 \d+ 步", "**{mfa}**（流水线第 {mfa_step} 步"),
    ("docs/TEST_REPORT.md", "媒体扫描边界演练", r"\*\*(\d+/\d+)\*\*（流水线第 \d+ 步）",
     "**{media_scan}**（流水线第 {media_scan_step} 步）"),
    # The browser leg's four figures live only in the a11y report and the chain log, so until now they were
    # copied by hand from the previous round -- and one of them (the step number) had quietly gone stale by
    # one when hold-drill widened the pipeline.
    ("docs/TEST_REPORT.md", "权威运行（",
     r"浏览器结果为 `release-evidence/browser-a11y-[0-9TZ]+/report\.json`",
     "浏览器结果为 `{browser_report}`"),
    ("docs/TEST_REPORT.md", "真实浏览器验收", r"（第 \d+ 步）", "（第 {browser_step} 步）"),
    # The console sentence was hand-written for several rounds, and it had drifted: it counted the report's
    # deduplicated lines as if they were events, and attributed a refusal to the wrong page. Its four
    # figures are now cells, so the sentence can only be stamped from what `report.json` actually holds.
    ("docs/TEST_REPORT.md", "真实浏览器验收", r"console 留痕 (\d+) 行", "console 留痕 {console_lines} 行"),
    ("docs/TEST_REPORT.md", "真实浏览器验收", r"网络层自己记的 (\d+) 行、应用侧自己打的 (\d+) 行",
     "网络层自己记的 {console_network_lines} 行、应用侧自己打的 {console_app_lines} 行"),
    ("docs/TEST_REPORT.md", "真实浏览器验收", r"按状态分 `([^`]+)`", "按状态分 `{console_status_breakdown}`"),
    ("docs/TEST_REPORT.md", "真实浏览器验收", r"每个状态各有几个标签 `([^`]+)`",
     "每个状态各有几个标签 `{console_labels}`"),
    # Lines and events are different axes, and conflating them is what made the old sentence claim six
    # refusals for what the server answered as eighteen. The request-side figures (`refusal_events`,
    # `refusal_lines`, `refusal_endpoints`, `refusal_top`) are read by the reader but are deliberately NOT
    # cells yet: `report.json` only started carrying `refusal_counts` with this round's harness change, so
    # the newest certified report cannot fill them and a cell would refuse the whole stamping round. They
    # stay cited prose off `scripts/server_refusal_census.py` until a chain that recorded them becomes the
    # authority -- then this is a table-only swap.
    ("docs/TEST_REPORT.md", "真实浏览器验收", r"共 (\d+) 个视图记录 / (\d+) 次 axe 扫描",
     "共 {browser_views} 个视图记录 / {browser_scans} 次 axe 扫描"),
    ("docs/TEST_REPORT.md", "真实浏览器验收", r"(\d+) 个 hidden 元素、(\d+) 个仍在渲染",
     "{browser_hidden} 个 hidden 元素、{browser_rendered_hidden} 个仍在渲染"),
    ("docs/TEST_REPORT.md", "主体权利走查", r"导出文件 [\d/ ]+ 字节", "导出文件 {browser_export_bytes} 字节"),
    # ---------------------------------------------------------------- docs/FINAL_RELEASE_STATUS.md
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"\*\*(\d+) steps PASS and (\d+) recorded as skipped\*\* across (\d+) rows",
     "**{pass} steps PASS and {skipped} recorded as skipped** across {rows} rows"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"commit `([0-9a-f]{7})`", "commit `{short}`"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z → \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
     "{started} → {ended}"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"release-evidence/acceptance-(\d{8}T\d{6}Z)/", "release-evidence/acceptance-{stamp}/"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r'host_load="([^"]*)"', 'host_load="{host_load}"'),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"Docker VM (\d+) vCPU", "Docker VM {cpus} vCPU"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"disk_free_kb=(\d+)", "disk_free_kb={disk}"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"with (\d+) prior green runs on this host", "with {green_count} prior green runs on this host"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"\((`[0-9a-f]{7}`(?:, `[0-9a-f]{7}`)*) — older", "({green_list_en} — older"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"at ([\d/]+) rows", "at {green_rows} rows"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh` on a fresh database",
     r"and (\d+) judged-red SUMMARYs kept as findings \([^)]*\)",
     "and {red_count} judged-red SUMMARYs kept as findings ({red_days_en})"),
    ("docs/FINAL_RELEASE_STATUS.md", "The pipeline is",
     r"The pipeline is (\d+) rows wide now", "The pipeline is {rows} rows wide now"),
    # This sentence named a browser report that no cell owned, so it kept a 2026-09-27 stamp while the
    # figures around it came from the current authority -- and it asserted "the same commit" without any
    # machinery ever comparing the two git_commit fields. Both halves are cells now.
    ("docs/FINAL_RELEASE_STATUS.md", "machine-readable record for the certified run",
     r"is at `release-evidence/browser-a11y-[0-9TZ]+/report\.json`", "is at `{browser_report}`"),
    ("docs/FINAL_RELEASE_STATUS.md", "machine-readable record for the certified run",
     r"stamped with the same commit `([0-9a-f]{7})`", "stamped with the same commit `{browser_commit_short}`"),
    # ---------------------------------------------------------------- docs/CHANGELOG_COST500.md
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"acceptance-(\d{8}T\d{6}Z)/", "acceptance-{stamp}/"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"commit `([0-9a-f]{7})`", "commit `{short}`"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r'host_load="([^"]*)"', 'host_load="{host_load}"'),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"Docker VM (\d+) vCPU", "Docker VM {cpus} vCPU"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"(\d+) 行里 (\d+) 步 PASS", "{rows} 行里 {pass} 步 PASS"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"前序 (\d+) 次 `FAIL=0`", "前序 {green_count} 次 `FAIL=0`"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"次 `FAIL=0` 为 (`[0-9a-f]{7}`(?:→`[0-9a-f]{7}`)*)", "次 `FAIL=0` 为 {green_list_arrow}"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"行数 (\d+)→(\d+)", "行数 {narrowest_rows}→{widest_rows}"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"(\d+) 份判红 SUMMARY 留档（[^）]*）", "{red_count} 份判红 SUMMARY 留档（{red_days_cn}）"),
    # ---------------------------------------------------------------- docs/CODE_WALKTHROUGH.md + runbook
    ("docs/CODE_WALKTHROUGH.md", "本轮实测",
     r"(\d+) 个文件、`pytest -q tests/unit --collect-only -q` 读出 (\d+) 个收集实例",
     "{unit_files} 个文件、`pytest -q tests/unit --collect-only -q` 读出 {unit_passed} 个收集实例"),
    ("docs/E2E_ACCEPTANCE_RUNBOOK.md", "本轮实测",
     r"本轮实测 (\d+) 个收集实例", "本轮实测 {unit_passed} 个收集实例"),
]

DERIVED = ("short", "green_count", "green_list_cn", "green_list_arrow", "green_list_en", "green_rows",
           "narrowest_rows", "widest_rows", "repeat_width", "repeat_times", "red_count", "red_days_cn",
           "red_days_en", "run_date", "unit_files", "mfa_step", "media_scan_step", "browser_commit_short")

# What the reader prints when a step's log carries no such figure. An absent reading is not a zero reading,
# so these never become prose: main() refuses the round on any consumed one, and resolve() re-checks the
# rendered span in case a template gains a sentinel-shaped literal.
SENTINELS = ("?", "LOG-MISSING", "NOT-FOUND", "MISSING")
_SENTINEL_RE = re.compile("|".join(re.escape(word) for word in SENTINELS))

def compiled_cells(values: dict) -> list[tuple[str, str, re.Pattern, str]]:
    """The table ready to run, after the 'nothing may be invented' check on the values."""
    missing = sorted(required_keys() - set(values))
    if missing:
        raise SystemExit(f"stamp: no reading for {missing} -- refusing to write")
    return [(rel, marker, re.compile(pattern), template) for rel, marker, pattern, template in CELLS]


def template_keys(template: str) -> set[str]:
    return {name for _lit, name, _fmt, _conv in string.Formatter().parse(template) if name}


def required_keys() -> set[str]:
    """Every name any template in the table asks for, read off the table itself.

    The resident test feeds a synthetic value per name and checks that each cell still resolves, so a
    pattern that no longer matches the prose -- or a cell that quietly matches twice -- fails the ladder
    instead of failing a stamping round at the moment the record is being written.
    """
    out = set()
    for _rel, _marker, _pattern, template in CELLS:
        out |= template_keys(template)
    return out


def _reader_module(root: pathlib.Path):
    """The figure reader, loaded by path so --root fully relocates the tool."""
    reader = root / "scripts/stamp_release_faces.py"
    if not reader.exists():
        raise SystemExit(f"stamp: the figure reader is not at {reader} -- --root must name a tree")
    spec = importlib.util.spec_from_file_location("stamp_release_faces", reader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _guard_module(root: pathlib.Path):
    """The gate's own module, loaded by path.

    Imported rather than copied: `archived_runs`/`split`/`days` are the rule the release-record gate
    applies, and a second implementation of that rule here would be free to disagree with it -- which is
    how the sentence this tool stamps drifted in the first place.
    """
    guard = root / "tests/unit/test_release_record_consistency.py"
    if not guard.exists():
        raise SystemExit(f"derive: the release-record gate is not at {guard} -- --root must name a tree")
    spec = importlib.util.spec_from_file_location("release_record_guard", guard)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def derive(root: pathlib.Path, figures: dict) -> dict:
    """The aggregate figures, from the guard's own rule, for the run being certified.

    Reads the tracked archive the same way `tests/unit/test_release_record_consistency.py` does, so the
    number stamped here and the number the gate recomputes cannot be two different claims.
    """
    guard = _guard_module(root)
    runs = guard.archived_runs()
    certified = figures["stamp"]
    run_date = f"{certified[0:4]}-{certified[4:6]}-{certified[6:8]}"
    if not any(r["stamp"] == certified for r in runs):
        raise SystemExit(f"derive: {certified} is not a tracked run -- `git add -f` its SUMMARY before stamping")
    greens, reds = guard.split(runs, certified)
    if not greens:
        raise SystemExit("derive: the archive holds no prior green run to enumerate")
    face_line = guard.sentence_face(guard.CHECKLIST)
    repeat = re.search(r"同样 (\d+) 行绿了 (\d+) 次", face_line)
    if not repeat:
        raise SystemExit("derive: the checklist's rhetorical row-width figure could not be read")
    width = int(repeat.group(1))
    rows = [g["rows"] for g in greens]
    days = guard.days(reds)
    cn = "、".join(f"{k[0:2]} 年 {k[2:4]} 月 {k[4:6]} 日 {n} 份" for k, n in sorted(days.items()))
    en = ", ".join(f"{k[0:2]}-{k[2:4]}-{k[4:6]} = {n}" for k, n in sorted(days.items()))
    stamps = [g["stamp"] for g in greens]
    dates = [dt.datetime.strptime(s[:8], "%Y%m%d").strftime("%Y-%m-%d") for s in stamps]
    if dates != sorted(dates):
        raise SystemExit(f"derive: the archived runs are not in date order: {dates}")
    if dates and dates[-1] > run_date:
        raise SystemExit(f"derive: a prior green run is dated after the certified run ({dates[-1]} > "
                         f"{run_date}) -- the record would certify a run that is not the newest")
    pipeline = (root / "scripts/acceptance-all.sh").read_text(encoding="utf-8", errors="replace")
    order = re.findall(r'^run_step "([a-z0-9-]+)"', pipeline, re.M)
    steps = {}
    for name in ("mfa-drill", "media-scan-drill"):
        if name not in order:
            raise SystemExit(f"derive: {name!r} is no longer dispatched by run_step in acceptance-all.sh")
        steps[name] = order.index(name) + 1
    unit_files = len(list((root / "tests/unit").glob("test_*.py")))
    # The head of the status file claims the browser record carries "the same commit" as the certified run.
    # That claim is only worth stamping if it is true, so the equality is checked here rather than written
    # into a sentence: when the a11y leg ran on a different tree than the one SUMMARY records, the round
    # refuses and the sentence keeps last round's reading instead of becoming a fabrication.
    browser_commit = figures.get("browser_commit")
    if browser_commit in (None, "NOT-FOUND", "MISSING"):
        raise SystemExit(f"derive: the certified run {certified} has no tracked browser report to point at "
                         "-- the faces quote a machine-readable a11y record, so `git add -f` its report.json")
    if browser_commit[:7] != figures["commit"][:7]:
        raise SystemExit(f"derive: the browser record is stamped {browser_commit[:7]} but the certified run "
                         f"records {figures['commit'][:7]} -- the a11y leg did not test this tree")
    out = {
        "short": figures["commit"][:7],
        "browser_commit_short": browser_commit[:7],
        "green_count": len(greens),
        "green_list_cn": ", ".join(f"`{g['commit']}`" for g in greens),
        "green_list_arrow": "→".join(f"`{g['commit']}`" for g in greens),
        "green_list_en": ", ".join(f"`{g['commit']}`" for g in greens),
        "green_rows": "/".join(str(r) for r in rows),
        "narrowest_rows": min(rows),
        "widest_rows": max(rows),
        "repeat_width": width,
        "repeat_times": sum(1 for r in rows if r == width),
        "red_count": len(reds),
        "red_days_cn": cn,
        "red_days_en": en,
        "run_date": run_date,
        "unit_files": unit_files,
        "mfa_step": steps["mfa-drill"],
        "media_scan_step": steps["media-scan-drill"],
    }
    absent = sorted(set(DERIVED) - set(out))
    if absent:
        raise SystemExit(f"derive: {absent} is in the table but derive() never computes it")
    return out


def resolve(cells, root: pathlib.Path, values: dict):
    """Apply every cell in memory and report each miss. Writes nothing.

    Returns (problems, new_texts, unchanged): `unchanged` counts substitutions whose rendered text already
    matches, which is what makes a re-run idempotent instead of a second, different claim.
    """
    problems: list[str] = []
    work: dict[str, list[str]] = {}
    unloadable: set[str] = set()
    unchanged = 0
    for rel, marker, pattern, template in cells:
        if rel in unloadable:
            continue
        if rel not in work:
            path = root / rel
            if not path.exists():
                problems.append(f"{rel}: face is missing")
                unloadable.add(rel)
                continue
            work[rel] = path.read_text(encoding="utf-8").splitlines(keepends=True)
        lines = work[rel]
        hits = [i for i, line in enumerate(lines) if marker in line]
        if len(hits) != 1:
            problems.append(f"{rel}: marker {marker!r} selects {len(hits)} lines, must be 1")
            continue
        idx = hits[0]
        body = lines[idx]
        found = list(pattern.finditer(body))
        if len(found) != 1:
            problems.append(f"{rel}: {pattern.pattern!r} matched {len(found)} times on the marker line, "
                            f"must be 1")
            continue
        try:
            rendered = template.format(**values)
        except KeyError as exc:
            problems.append(f"{rel}: template needs {exc.args[0]!r}, which was never read")
            continue
        except (IndexError, ValueError) as exc:
            problems.append(f"{rel}: template {template!r} is not a plain named substitution ({exc})")
            continue
        for word in _SENTINEL_RE.findall(rendered):
            problems.append(f"{rel}: rendered cell carries an unread placeholder {word!r}: {rendered!r}")
        if rendered == found[0].group(0):
            unchanged += 1
        lines[idx] = body[:found[0].start()] + rendered + body[found[0].end():]
    return problems, {rel: "".join(lines) for rel, lines in work.items()}, unchanged


def apply(cells, root: pathlib.Path, values: dict):
    """Gate first, write once, then read the disk back. A miss leaves every face byte-identical."""
    problems, texts, unchanged = resolve(cells, root, values)
    if problems:
        return {"ok": False, "problems": problems, "written": [], "unchanged": unchanged,
                "reason": f"{len(problems)} cell(s) did not resolve, so nothing was written"}
    written = []
    preimages = {rel: (root / rel).read_text(encoding="utf-8") for rel in texts}
    try:
        for rel, text in texts.items():
            (root / rel).write_text(text, encoding="utf-8")
            written.append(rel)
    except OSError as exc:                       # a half-written round is the thing this function exists to avoid
        for rel, text in preimages.items():
            (root / rel).write_text(text, encoding="utf-8")
        return {"ok": False, "problems": [], "written": [], "rolled_back": written,
                "reason": f"write failed after {len(written)} face(s): {exc}"}
    verify = []
    for rel, marker, pattern, template in cells:
        if rel not in texts:
            continue
        lines = (root / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        hits = [i for i, line in enumerate(lines) if marker in line]
        if len(hits) != 1:
            verify.append(f"{rel}: after writing, marker {marker!r} selects {len(hits)} lines")
    return {"ok": not verify, "problems": verify, "written": sorted(texts), "unchanged": unchanged,
            "reason": "ok" if not verify else "post-write re-read disagrees"}


# ------------------------------------------------------------------ self-test ----

FIXTURE = {
    "docs/a.md": "row: authority `release-evidence/acceptance-X/` had 3 greens\n",
    "docs/b.md": "row: authority `release-evidence/acceptance-X/` said 1\n",
}
FIX_CELLS = [
    ("docs/a.md", "authority", re.compile(r"had (\d+) greens"), "had {green_count} greens"),
    ("docs/b.md", "authority", re.compile(r"said (\d+)"), "said {red_count}"),
]
VALUES = {"green_count": 4, "red_count": 2}


def _tree(root: pathlib.Path, cells=None, stray_marker_line=False, doubled_pattern=False,
          drop_face=False):
    """A two-face tree shaped like the real one, carrying exactly the fault the arm names."""
    for rel, text in FIXTURE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == "docs/a.md" and stray_marker_line:
            text += "authority on a second line\n"
        if rel == "docs/a.md" and doubled_pattern:
            text = text.rstrip("\n") + "\nhad 9 greens authority\n"
        path.write_text(text, encoding="utf-8")
    table = list(cells if cells is not None else FIX_CELLS)
    if drop_face:
        table.append(("docs/gone.md", "authority", re.compile(r"said (\d+)"), "said {red_count}"))
    return [(rel, marker, re.compile(pattern), template) for rel, marker, pattern, template in table]


def self_test() -> int:
    """Each arm names the fault and what must hold on disk afterwards.

    Every abort arm also checks that the *other* face stayed byte-identical: a gate that refuses the bad
    cell but writes the good one is exactly the half-stamped round the module docstring forbids. The
    unread-placeholder arm exists because `resolve` is what turns a missing reading into prose.
    """
    failures = []

    def report(name, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {name}{'' if ok else '  << ' + detail}")
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cells = _tree(root)
        result = apply(cells, root, VALUES)
        landed = (root / "docs/a.md").read_text(encoding="utf-8")
        report("happy path stamps both faces",
               result["ok"] and "had 4 greens" in landed
               and sorted(result["written"]) == ["docs/a.md", "docs/b.md"], str(result))
        again = apply(cells, root, VALUES)
        report("a stamped tree re-applies as a no-op instead of a second claim",
               again["ok"] and again["unchanged"] == 2, str(again))

    def aborted(name, build, why):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cells = build(root)
            result = apply(cells, root, VALUES)
            stayed = (root / "docs/b.md").read_text(encoding="utf-8") == FIXTURE["docs/b.md"]
            report(name, not result["ok"] and not result["written"] and stayed,
                   f"{why}: ok={result['ok']} written={result['written']} other-face-untouched={stayed}")

    aborted("a pattern matching twice aborts the whole round", lambda r: _tree(r, doubled_pattern=True),
            "two matches")
    aborted("a marker selecting two lines aborts", lambda r: _tree(r, stray_marker_line=True), "two lines")
    aborted("a missing face aborts", lambda r: _tree(r, drop_face=True), "no such file")
    aborted("a template key that was never read aborts",
            lambda r: _tree(r, cells=[("docs/a.md", "authority", r"had (\d+) greens",
                                       "had {never_computed} greens")]), "no reading")
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cells = _tree(root)
        result = apply(cells, root, {**VALUES, "green_count": "LOG-MISSING"})
        stayed = (root / "docs/b.md").read_text(encoding="utf-8") == FIXTURE["docs/b.md"]
        report("an unread reading is refused before any face moves",
               not result["ok"] and not result["written"] and stayed, str(result))
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cells = _tree(root)
        problems, texts, unchanged = resolve(cells, root, VALUES)
        untouched = all((root / rel).read_text(encoding="utf-8") == FIXTURE[rel] for rel in texts)
        report("resolve() writes nothing even when every cell resolves", not problems and unchanged == 0
               and untouched, f"{problems} {untouched}")
    print(f"self-test: 8 arms, failures: {failures or 'none'}")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT), help="tree to stamp; lets the engine run against a fixture")
    ap.add_argument("--figures", help="JSON file of readings, or - for stdin; default is the reader's run")
    ap.add_argument("--run", help="stamp of the run to read when --figures is not given")
    ap.add_argument("--check", action="store_true", help="resolve every cell and report, write nothing")
    ap.add_argument("--apply", action="store_true", help="gate, write, re-read")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.check and args.apply:
        raise SystemExit("stamp: --check writes nothing and --apply does; pick one")
    root = pathlib.Path(args.root)
    if args.figures:
        figures = json.loads(pathlib.Path(args.figures).read_text(encoding="utf-8") if args.figures != "-"
                             else sys.stdin.read())
    else:
        reader = _reader_module(root)
        tracked = list(reader.runs())
        if args.run:
            chosen = next((r for r in tracked if r["stamp"] == args.run), None)
            if chosen is None:
                raise SystemExit(f"stamp: {args.run} is not a tracked run: {[r['stamp'] for r in tracked]}")
        else:
            greens = [r for r in tracked if r["fails"] == 0]
            if not greens:
                raise SystemExit("stamp: the archive holds no all-green run to read")
            chosen = max(greens, key=lambda r: r["stamp"])
        figures = reader.figures(chosen)
    values = dict(derive(root, figures))
    conflicting = sorted(k for k in set(values) & set(figures) if values[k] != figures[k])
    if conflicting:
        raise SystemExit(f"stamp: {[(k, values[k], figures[k]) for k in conflicting]} -- the aggregate this "
                         f"tool computes and the archived record disagree, so neither may be stamped")
    values.update(figures)
    # Refuse on the readings the table actually consumes. A run that predates a step carries unread cells no
    # one asked for, and aborting on those would bury the ones that matter; a consumed cell carrying a
    # sentinel is a fabricated reading, so it stops the round here and resolve() re-checks rendered text.
    unread = sorted(k for k, v in values.items() if isinstance(v, str) and v in SENTINELS)
    consumed = sorted(set(unread) & required_keys())
    if consumed:
        raise SystemExit(f"stamp: acceptance-{figures['stamp']} has no reading for the consumed cells "
                         f"{consumed} -- the run's own per-step logs are the source and nothing here guesses")
    if unread:
        extra = sorted(set(unread) - set(consumed))
        if extra:
            print(f"note: this run has unread readings the table does not stamp: {extra}")
    cells = compiled_cells(values)
    if args.apply:
        result = apply(cells, root, values)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    problems, texts, unchanged = resolve(cells, root, values)
    print(f"authority: acceptance-{figures['stamp']}  cells: {len(cells)} across {len(texts)} faces; "
          f"resolved {len(cells) - len(problems)}, unchanged {unchanged}, misses {len(problems)}")
    for problem in problems:
        print(f"  MISS {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
