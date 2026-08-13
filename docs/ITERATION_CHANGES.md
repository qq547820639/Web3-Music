# 迭代变更清单（寇豆码 / Kou）

> 本次为**有边界、可验证**的代码优化实施。依据 `docs/CODE_WALKTHROUGH.md` §4（代码质量问题清单）与 §5（UX 深化清单），逐项落地 A 组（后端确定性修复）与 B 组（前端 UX）任务。
> 原则：最小变更、不重构无关代码、不改动现有测试与架构契约。

## 验收命令执行结果（改完全绿）

```bash
cd /Volumes/Extra/CodeProj/web3_music
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python -m compileall -q services tests scripts   # 通过（无输出即成功）
find services -name '*.js' -print0 | xargs -0 -n1 /Users/panhao/.workbuddy/binaries/node/versions/22.22.2/bin/node --check   # 通过
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python -m pytest -q tests/unit                        # 28 passed in ~3.1s
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python scripts/architecture-audit.py                  # architecture contracts valid
```

---

## A 组 — 后端确定性修复（10 项，全部完成）

| # | 报告编号 | 文件 | 改动摘要 |
|---|---|---|---|
| A1 | P2-5 | `services/api/app/domain/patches.py` | `get_pointer` 与 `apply_patch` 的数组下标 `int(part)` 越界（`IndexError`/`ValueError`/`KeyError`/`TypeError`）改为捕获并抛 `PatchError`；`add` 到数组越界下标也统一转 `PatchError`。不再让 JSON Patch 数组越界变成 500。 |
| A2 | P2-6 | `services/api/app/domain/commerce.py` | `issue_credits` / `revoke_credits` 的 `operation_key` 判重由普通 `SELECT` 改为 `SELECT ... FOR UPDATE`（对齐 `ledger.post()`）；同时对 `ledger_accounts` 的读锁加 `FOR UPDATE`，避免并发撞唯一约束。 |
| A3 | P2-4 | `services/api/app/routers/market.py` | `review_submissions` / `update_submission` 的 `except Exception → 403` 拆开：捕获 `psycopg2.Error`，仅当消息含 `brief owner required` 时返回 403，其余真实 DB 故障直接抛出 → 500/重试。 |
| A4 | P2-7 | `services/api/app/main.py` + `common.py` | 删除 `main.py` 内重复定义的 `serialize` / `audit` / `setting_enabled`，统一从 `common.py` 引用；顺手移除 `main.py` 中不再使用的 `Decimal` 导入。 |
| A5 | P2-10 | `services/api/app/settings.py` | `DB_POOL_MAX` 默认值 `12` → `16`，与 `.env.example` 对齐。 |
| A6 | P2-12 | `services/api/app/main.py` | 废弃的 `@app.on_event("startup"/"shutdown")` 改为 FastAPI `lifespan`（`@asynccontextmanager`），startup/shutdown 逻辑不变。 |
| A7 | P2-13 | `services/api/app/main.py` | 登录限流由 `rq.incr` + `rq.expire` 两条非原子命令改为单条 Lua 脚本 `INCR`+`EXPIRE`（`_login_incr`），避免两命令间崩溃导致邮箱被永久锁死。 |
| A8 | P0-3 | `services/api/app/main.py` + `shared/contracts/openapi-v13.{json,yaml}` | 声明 `HTTPBearer`（`JWTBearer`）与 `APIKeyCookie`（`SessionCookie`，绑定 `resonance_access`）两种安全方案（`auto_error=False`，仅文档化，鉴权仍走 `get_user/get_actor`）；离线调用 `app.openapi()` 重新导出 JSON+YAML，`securitySchemes` 非空且二者一致（69 paths 不变）。 |
| A9 | P0-2 | `services/payment-emulator/main.py` | `processing` / `requires_action` 场景在 `PAYMENT_TIMEOUT_SECONDS`（默认 60s，可用环境变量覆盖）后回调 `payment_intent.failed`，避免订单永久卡 `payment_pending`；注释说明订单侧对账兜底口径（`market.py` 的 `payment_webhook` 以 `failed` 为释放信号）。 |
| A10 | P1-4 | `main.py` / `routers/assets.py` / `routers/market.py` / `common.py` | 新增统一分页常量 `PAGE_LIMIT_DEFAULT=100` / `PAGE_LIMIT_MAX=200`；为主要 list 端点加 `limit`/`offset` Query 参数（`list_projects`、`list_assets`、订单/许可/交付/结算/工单/市场报价/自有报价/品牌任务投稿列表等），SQL 追加 `LIMIT %s OFFSET %s`，防止全表拉取。 |

---

## B 组 — 前端（4 项，全部完成）

> 前置：npx/prettier 因沙箱代理网络 `EIDLETIMEOUT` 无法下载（npm registry 慢到超时）。改为用本地 npm 缓存中已存在的 **`@babel/parser` + `@babel/generator`（JS）与 `postcss`（CSS）** 做等价还原，效果等同 prettier 的多行可维护源码；`node --check` 全绿。

| # | 任务 | 文件 | 改动摘要 |
|---|---|---|---|
| B1 | 美化（beautify） | `services/web/app.js`、`services/web/styles.css`、`services/admin/admin.js`、`services/admin/styles.css` | 压缩单文件还原为多行可维护源码：app.js 118→约 1300 行、admin.js 17→270 行、styles.css 4→900+ 行、admin/styles.css 1→337 行。语义等价（Babel AST 往返；admin.js 原始/美化 AST 类型序列逐项一致）。 |
| B2 | 错误人话化（P1-6） | `services/web/app.js` | `api()` 错误分支改为 `humanizeError(status, detail)` + `ERROR_MESSAGES` 错误码→中文文案映射表；未命中时回退原始 `message` 字符串、去掉裸 JSON。 |
| B3 | 加载态 | `services/web/app.js` + `services/web/styles.css` | 新增 `setLoading()` 按钮级加载（禁用 + 防重复提交）+ 轻量 spinner（`button.loading` + `@keyframes spin`）；覆盖登录、chat/创作、保存、质量评估、生成报价、提交任务、购买/支付、退款、新建工单/偏好等关键异步提交。 |
| B4 | 替换 prompt/confirm（T05 核心） | `services/web/app.js` | 新增 `askDialog()`（带字段、默认值、必填校验、错误提示）与 `confirmDialog()`；将 app.js 的 **16 处 `prompt()` + 4 处 `confirm()` 全部替换**为 `<dialog>` 表单/确认框（覆盖新建项目、新建分支、许可人名称、退款原因、工单主题/描述，以及评论、证据、报价、品牌任务、Master 确认、权利复核等）。替换后 app.js 内 `prompt(` / `confirm(` 出现次数为 0。 |

---

## 变更文件总览

后端：
- `services/api/app/domain/patches.py`
- `services/api/app/domain/commerce.py`
- `services/api/app/routers/market.py`
- `services/api/app/routers/assets.py`
- `services/api/app/common.py`
- `services/api/app/main.py`
- `services/api/app/settings.py`
- `services/payment-emulator/main.py`
- `shared/contracts/openapi-v13.json`
- `shared/contracts/openapi-v13.yaml`

前端：
- `services/web/app.js`
- `services/web/styles.css`
- `services/admin/admin.js`
- `services/admin/styles.css`

新增：
- `docs/ITERATION_CHANGES.md`（本文件）

---

## 遗留点 / 风险提示（供主理人与 QA）

1. **B1 使用 Babel+postcss 而非 prettier**：因网络无法下载 prettier，采用解析器级等价还原，语义等价、`node --check` 全绿；输出风格与 prettier 略有差异（个别 `if(...)stmt;else{...}` 同行），不影响功能。
2. **A8 OpenAPI 由离线 `app.openapi()` 重导出**：非运行中 `/openapi.json` curl，内容应与真实运行一致；`securitySchemes` 已非空（`JWTBearer`/`SessionCookie`），JSON 与 YAML 一致。若 CI 使用 `export-openapi.sh`（需运行实例），结果应与本文件一致。
3. **A9 支付超时回调用 `payment_intent.failed`**（对齐 `market.py` 已处理的 webhook 类型）；超时默认 60s，可通过 `PAYMENT_TIMEOUT_SECONDS` 调整。订单侧对账兜底口径依赖该 `failed` 回调释放订单/预约，未另加定时对账任务（原报告 P0-2 的"定时对账"更彻底方案未实施，属可选后续）。
4. **A10 分页为 `limit/offset`**（非 cursor），前端暂未接分页 UI，仅后端防全表拉取；`PAGE_LIMIT_DEFAULT=100`/`PAGE_LIMIT_MAX=200`。`admin` 控制面列表（`admin_v12.py`）未在本次分页范围内，如需要可后续同法补齐。
5. **前端改动未做浏览器 E2E**：本环境无 Docker/浏览器，`app.js` 的 `<dialog>` 交互、按钮加载态、错误文案仅经 `node --check` 语法校验与人工走查，未在真实浏览器点验；建议在有浏览器/Docker 的环境跑一遍核心流程（登录→建项目→chat→报价→提交任务→支付→退款→工单）。
6. **pytest 仍为 28 passed**（未新增用例）；本次未改动 `tests/`，也未新增针对 A2/A7/A8 的并发/契约单测，如需可在后续补充（本任务明确要求"不得新增失败"且"最小变更"）。
