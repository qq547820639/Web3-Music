"""Correct the present-tense figures the census flagged and I recomputed by hand.

All-or-nothing: every (old, new) pair must match its file exactly once, or nothing is written.
"""
import pathlib
import sys

PAIRS = [
    # docs/RELEASE_CHECKLIST.md
    ("docs/RELEASE_CHECKLIST.md", "`git_commit=cb8b901`", "`git_commit=01147cf`"),
    ("docs/RELEASE_CHECKLIST.md",
     "（权威运行第 20 步读数 `25/25 completed, error rate 0.0%, p50 4.1s / p95 5.19s`；"
     "同一轮第 19 步的 100 任务 `p50 4.13s / p95 5.47s`。这两步各自已经跑过十三次／十八次绿线",
     "（权威运行第 21 步读数 `25/25 completed, error rate 0.0%, p50 4.15s / p95 4.18s`；"
     "同一轮第 20 步的 100 任务 `p50 4.09s / p95 5.36s`。这两步各自已经跑过 32 次（第三方适配器往返）"
     "／38 次（100 任务回归）绿线"),
    ("docs/RELEASE_CHECKLIST.md", "演练 `scripts/mfa_drill.py` 56/56", "演练 `scripts/mfa_drill.py` 79/79"),
    ("docs/RELEASE_CHECKLIST.md", "单元测试 229 条", "单元测试 478 条"),
    ("docs/RELEASE_CHECKLIST.md",
     "共 70 次 axe 扫描、96 个视图记录，结果 **critical 0 / serious 0**，moderate 52 项集中在 "
     "`heading-order`（36）、`landmark-one-main`（8）、`region`（8）三条（未达本 Gate 判据，作为后续项列出，不算已修）",
     "共 92 次 axe 扫描、118 个视图记录，结果 **critical / serious / moderate 三档全零**——那三条 "
     "`heading-order`、`landmark-one-main`、`region` 在后续轮次被逐条修掉，moderate 也在同一轮升为阻断档"),
    ("docs/RELEASE_CHECKLIST.md", "本轮 96 个记录里有 5 类", "本轮 118 个记录里有 5 类"),
    ("docs/RELEASE_CHECKLIST.md", "（手机档 48 个记录）", "（手机档 59 个记录）"),
    ("docs/RELEASE_CHECKLIST.md",
     "只有 5 条写路由不带角色检查（登录、刷新、第二步挑战、两条回调），它们各自的保护载体（口令校验、"
     "刷新会话校验、按账号限流、HMAC 签名）",
     "只有 7 条写路由不带角色检查（登录、刷新、第二步挑战、两条回调、两条公开投诉收件），"
     "它们各自的保护载体（口令校验、刷新会话校验、按账号限流、HMAC 签名、收件限流）"),
    # docs/TEST_REPORT.md
    ("docs/TEST_REPORT.md", "`--self-test` 三类注入对照 + 16 条判决函数单测",
     "`--self-test` 三类注入对照 + 17 条判决函数单测"),
    # docs/FINAL_RELEASE_STATUS.md -- the early block is the 2026-09-26 round's reading; its umbrella
    # sentence claimed the whole file, which stopped being true the moment the faces moved to a newer run.
    ("docs/FINAL_RELEASE_STATUS.md",
     "> the CI jobs as dispatched-and-unverified; every number quoted in this file comes from the\n> local run above.",
     "> the CI jobs as dispatched-and-unverified; every number quoted in **this section** comes from\n"
     "> the local run described just above it, and readings taken in later rounds live in the dated\n"
     "> sections below and in the stamped cells."),
    # docs/CODE_WALKTHROUGH.md
    ("docs/CODE_WALKTHROUGH.md", "`docker-compose.yml`（+3 个 override）", "`docker-compose.yml`（+4 个覆层）"),
    ("docs/CODE_WALKTHROUGH.md", "与后端 69 条 API 基本对齐", "与后端 100 条 /api 路由基本对齐"),
    ("docs/CODE_WALKTHROUGH.md", "| `acceptance` | 4 个 E2E 测试（默认/商业/Provider 契约/Payment 契约） |",
     "| `acceptance` | 5 个测试模块（默认/商业/Provider 契约/Payment 契约/跨租户隔离） |"),
    ("docs/CODE_WALKTHROUGH.md",
     "- JSON Schema：顶层 8 个（song-spec-runtime、generation-job-v3、license-v1、order-v1、rights-evidence-v1、"
     "payment-event-v1、product-event-v1、brand-brief-v1、credit-hold）+ `design-reference/` 8 个旧版。",
     "- JSON Schema：顶层 10 个（song-spec-runtime-v1、generation-job-v3、license-v1、order-v1、rights-evidence-v1、"
     "payment-event-v1、product-event-v1、provider-submit-v1、brand-brief-v1、credit-hold）+ `design-reference/` 10 个旧版。"),
    ("docs/CODE_WALKTHROUGH.md",
     "- 6 个迁移：001（25 表核心域）、002（26 表市场/商业）、003（跨租户外键 + Payout 不可变 + License 模板 RLS）、"
     "004（Brand Award 流程 + SECURITY DEFINER 函数）、005（auth_sessions/moderation_decisions + 不可变触发器）、"
     "006（容量索引）。",
     "- 22 个迁移（`db/migrations/001…022.sql`），前六支：001（核心域）、002（市场/商业）、"
     "003（跨租户外键 + Payout 不可变 + License 模板 RLS）、004（Brand Award 流程 + SECURITY DEFINER 函数）、"
     "005（auth_sessions/moderation_decisions + 不可变触发器）、006（容量索引）。"),
    ("docs/CODE_WALKTHROUGH.md",
     "- `.github/workflows/ci.yml`：三 job —— `static-and-unit`、`compose-acceptance`（真实起 Docker + acceptance + "
     "contract + chaos + backup/restore）、`commercial-flow`。",
     "- `.github/workflows/ci.yml`：5 个 job —— `static-and-unit`、`compose-acceptance`（真实起 Docker + acceptance + "
     "contract + chaos + backup/restore）、`commercial-flow`、`browser-a11y`、`capacity-500`。"),
    ("docs/CODE_WALKTHROUGH.md", "后端 69 条 API 的丰富度", "后端 100 条 /api 路由的丰富度"),
]

by_file = {}
for rel, old, new in PAIRS:
    by_file.setdefault(rel, []).append((old, new))

staged = {}
for rel, pairs in by_file.items():
    path = pathlib.Path(rel)
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        hits = text.count(old)
        if hits != 1:
            print("ABORT: anchor hits %d in %s for %r" % (hits, rel, old[:60]))
            sys.exit(1)
        text = text.replace(old, new)
    staged[rel] = text

for rel, text in staged.items():
    pathlib.Path(rel).write_text(text, encoding="utf-8")
print("patched %d files, %d pairs" % (len(staged), len(PAIRS)))
