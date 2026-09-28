"""Cell substitution for the release-record faces -- see the module docstring in stamp_release_faces.py.

Kept separate from the reader so each face's cell table stays readable. A cell is (file, a marker that must
pick exactly one line, a regex that must match exactly once on that line, and a template built only from
readings the tool actually obtained). Any miss aborts the whole write: a half-stamped round is worse than a
refused one, and a `?` in a release record is a fabricated reading.
"""
from __future__ import annotations

import re

# Template keys come from figures(): stamp, commit, rows, pass, skipped, fails, started, ended, host_load,
# cpus, disk, fresh, unit_passed, tracked, listed, routes, matrix_writes, mfa, member, erasure,
# media_scan, report, hold, reconcile, lease, restore, regression, regression_ms, generic, generic_ms,
# plus derived: green_count, green_commits, green_rows, red_count, red_split, browser_step, short.
CELLS = [
    # ---------------------------------------------------------------- docs/RELEASE_CHECKLIST.md, the status line
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"release-evidence/acceptance-(\d{8}T\d{6}Z)/", "release-evidence/acceptance-{stamp}/"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"commit `([0-9a-f]7)`", "commit `{short}`"),
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
     r"(\d+) routes, (\d+) writes", "{routes} routes, {matrix_writes} writes"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"同一主机已有 (\d+) 次 `FAIL=0`", "同一主机已有 {green_count} 次 `FAIL=0`"),
    ("docs/RELEASE_CHECKLIST.md", "权威运行是 `scripts/acceptance-all.sh`",
     r"留档的判红共 (\d+) 份（[^）]*）", "留档的判红共 {red_count} 份（{red_days_cn}）"),
    ("docs/RELEASE_CHECKLIST.md", "media scan drill:",
     r"media scan drill: (\d+/\d+)", "media scan drill: {media_scan}"),
    # ---------------------------------------------------------------- docs/TEST_REPORT.md
    ("docs/TEST_REPORT.md", "权威运行（",
     r"\*\*(\d+) 步 PASS \+ (\d+) 步按开关跳过（共 (\d+) 行）\*\*", "**{pass} 步 PASS + {skipped} 步按开关跳过（共 {rows} 行）**"),
    ("docs/TEST_REPORT.md", "权威运行（", r"commit `([0-9a-f]7)`", "commit `{short}`"),
    ("docs/TEST_REPORT.md", "权威运行（", r"acceptance-(\d{8}T\d{6}Z)/", "acceptance-{stamp}/"),
    ("docs/TEST_REPORT.md", "权威运行（", r'host_load="([^"]*)"', 'host_load="{host_load}"'),
    ("docs/TEST_REPORT.md", "权威运行（", r"Docker VM (\d+) vCPU", "Docker VM {cpus} vCPU"),
    ("docs/TEST_REPORT.md", "发现记录同样保留", r"留档判红共 (\d+) 份（[^）]*）",
     "留档判红共 {red_count} 份（{red_days_cn}）"),
    ("docs/TEST_REPORT.md", "第二因子演练", r"\*\*(\d+/\d+)\*\*（流水线第 \d+ 步", "**{mfa}**（流水线第 {mfa_step} 步"),
    ("docs/TEST_REPORT.md", "媒体扫描边界演练", r"\*\*(\d+/\d+)\*\*（流水线第 \d+ 步）", "**{media_scan}**（流水线第 {media_scan_step} 步）"),
    # ---------------------------------------------------------------- docs/FINAL_RELEASE_STATUS.md
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh`",
     r"\*\*(\d+) steps PASS and (\d+) recorded as skipped\*\* across (\d+) rows",
     "**{pass} steps PASS and {skipped} recorded as skipped** across {rows} rows"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh`",
     r"commit `([0-9a-f]7)`", "commit `{short}`"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh`",
     r"release-evidence/acceptance-(\d{8}T\d{6}Z)/", "release-evidence/acceptance-{stamp}/"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh`",
     r'host_load="([^"]*)"', 'host_load="{host_load}"'),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh`",
     r"Docker VM (\d+) vCPU", "Docker VM {cpus} vCPU"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh`",
     r"disk_free_kb=(\d+)", "disk_free_kb={disk}"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh`",
     r"with (\d+) prior green runs on this host", "with {green_count} prior green runs on this host"),
    ("docs/FINAL_RELEASE_STATUS.md", "Authoritative run: `scripts/acceptance-all.sh`",
     r"and (\d+) judged-red SUMMARYs kept as findings \([^)]*\)",
     "and {red_count} judged-red SUMMARYs kept as findings ({red_days_en})"),
    ("docs/FINAL_RELEASE_STATUS.md", "The pipeline is",
     r"The pipeline is (\d+) rows wide now", "The pipeline is {rows} rows wide now"),
    # ---------------------------------------------------------------- docs/CHANGELOG_COST500.md
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"acceptance-(\d{8}T\d{6}Z)/", "acceptance-{stamp}/"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"commit `([0-9a-f]7)`", "commit `{short}`"),
    ("docs/CHANGELOG_COST500.md", "权威运行 `release-evidence/",
     r"(\d+) 份判红 SUMMARY 留档（[^）]*）", "{red_count} 份判红 SUMMARY 留档（{red_days_cn}）"),
    # ---------------------------------------------------------------- docs/CODE_WALKTHROUGH.md + runbook
    ("docs/CODE_WALKTHROUGH.md", "本轮实测",
     r"(\d+) 个文件、`pytest -q tests/unit --collect-only -q` 读出 (\d+) 个收集实例",
     "{unit_files} 个文件、`pytest -q tests/unit --collect-only -q` 读出 {unit_passed} 个收集实例"),
    ("docs/E2E_ACCEPTANCE_RUNBOOK.md", "本轮实测",
     r"本轮实测 (\d+) 个收集实例", "本轮实测 {unit_passed} 个收集实例"),
]

DERIVED = ("short", "green_count", "red_count", "red_days_cn", "red_days_en", "unit_files",
           "mfa_step", "media_scan_step")


def cells_for_run(figures: dict, extra: dict) -> list[tuple[str, str, re.Pattern, str]]:
    """The table, with the derived values folded in. Nothing here may invent a value."""
    values = dict(figures)
    values.update(extra)
    for key in DERIVED:
        if key not in values:
            raise SystemExit(f"stamp: derived value {key!r} was never computed -- refusing to write")
    return [(rel, marker, re.compile(pattern), template) for rel, marker, pattern, template in CELLS]
