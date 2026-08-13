# 第三阶段变更清单 · 交付可验证性收口

> 日期：2026-08-13 · 工程师：寇豆码（Kou）
> 范围：`docs/ITERATION_PLAN_PHASE3.md` C1/C2，最小变更、零第三方依赖、不破坏上一轮成果。

## 任务状态总览

| 编号 | 任务 | 状态 | 说明 |
|---|---|---|---|
| C1-1 | `scripts/acceptance-all.sh` 一键 E2E 验收脚本 | ✅ 完成 | POSIX-sh 语法、分节时间戳、失败即停+诊断、证据归档 |
| C1-2 | `docs/E2E_ACCEPTANCE_RUNBOOK.md` 部署主机 SOP | ✅ 完成 | 前置条件/分步执行/判读/Go-No-Go 清单/证据/排查 |
| C2-1 | 语义地标 + skip link（web + admin） | ✅ 完成 | header/nav/main/footer + lang + 跳到主内容 |
| C2-2 | `<dialog>` 焦点管理 | ✅ 完成 | 焦点移入 + 关闭还原；Escape/焦点陷阱由 `showModal()` 原生提供 |
| C2-3 | 动态元素 ARIA | ✅ 完成 | aria-label / aria-pressed / aria-selected / aria-current / role |
| C2-4 | 通知无障碍 | ✅ 完成 | toast `role=status aria-live=polite`；loading `aria-busy` |
| C2-5 | 键盘可用 + 雷达可见文本等价物 | ✅ 完成 | 雷达点可聚焦、SVG `role=img`、新增可见「维度列表」 |

## 逐文件变更

### C1-1 `scripts/acceptance-all.sh`（新建，可执行）

- `#!/usr/bin/env bash` + `set -euo pipefail`，先 `cd "$(dirname "$0")/.."`。
- `step()` 打印醒目的 `===` 分隔与 UTC 时间戳，并递增步骤号。
- `run_step <名称> <函数>` 在**当前 shell**执行函数（保留 `set -e` 首错即停语义），输出重定向到分步日志后再回显；失败打印 `该步骤失败，日志在 …`、写入部分摘要、收集证据后 `exit 1`。
- 按序编排（只调用既有脚本，不重复实现逻辑）：`static-verify` → `test.sh` → `docker compose up --build -d`+`ps` → 默认 `acceptance` → `contract-test` → `chaos-worker-recovery` → `backup.sh acceptance`+`RESTORE_CONFIRM=YES restore.sh backups/acceptance` → 复跑 `acceptance` → commercial 覆层 `acceptance-commercial` → 可选 `capacity-gate-500`（仅 `CAPACITY=1`）。
- 关键对齐：`RESTORE_CONFIRM=YES`（restore.sh 第 5 行要求）、`backup.sh` 以 `acceptance` 为子目录、capacity gate 前先 `down --remove-orphans` 释放端口（容量覆层会重起整套栈）。
- 全程 POSIX-sh 语法（无数组/`[[ ]]`/`local`/process substitution），通过 `sh -n` 与 `bash -n`；已用 mock `docker`+mock 脚本验证**成功路径与失败路径**（失败正确 `exit 1`、日志落在 `release-evidence/acceptance-<ts>/` 内）。
- 证据包：`SUMMARY.txt`（每步 PASS/FAIL + 起止时间戳）、`step-N-<name>.log`、`compose-logs.txt`、`commercial-compose-logs.txt`。

### C1-2 `docs/E2E_ACCEPTANCE_RUNBOOK.md`（新建）

含：前置条件（Docker/Compose v2 版本检查、资源规格、端口 8080/8000/8010/8020/4173/4174/54329/63799/9000/9001/9090 占用检查、`.env` 准备）、完整分步执行、每步判读表、**Go/No-Go 清单表格**、证据收集归档（指向 `release-evidence/`）、常见失败排查（端口冲突/镜像拉取/账本差异/额度泄漏/chaos 超时/商业覆层/容量），并引用 CI 的 `compose-acceptance`/`commercial-flow` 作为等价序列。

### C2-1 `services/web/index.html`、`services/admin/index.html`

- web 已有 `<html lang="zh-CN">`/`<header>`/`<nav>`/`<main>`；补 `<footer class="app-footer">`、`<main id="main" tabindex="-1">`、body 顶部 `<a class="skip-link" href="#main">跳到主内容</a>`、dialog 关闭按钮 `aria-label`、工作区 `<select>` `aria-label`。
- admin 同样补 footer、`id="main"`、skip link、`<nav aria-label="主导航">`；`<html lang="zh-CN">` 已有。
- 导航按钮补 `aria-label` + `aria-current`；市场页签补 `role="tablist"/role="tab"/aria-selected`。

### C2-2 `services/web/app.js`

- 新增 `rememberDialogTrigger`/`restoreDialogFocus`/`firstFocusable`；`showDialog` 打开后把焦点移入首个可聚焦元素（缺省落到关闭按钮）；全局 `$('#dialog').addEventListener('close', restoreDialogFocus)` 关闭后还原焦点到触发按钮。
- `askDialog`/`confirmDialog` 在 promise 开头 `rememberDialogTrigger()`（先于任何 `close()`）。
- `showModal()` 原生提供焦点陷阱与 `Escape` 关闭（代码内注明，不重复实现）。

### C2-3 `services/web/app.js`、`services/admin/admin.js`

- web：搜索框 `aria-label`、状态筛选按钮 `aria-pressed` + 容器 `role="group"`、分页按钮 `aria-label`、市场页签 JS 同步 `aria-selected`、主导航 JS 同步 `aria-current`、`<audio>` `aria-label`、设为 Master/时间点评论按钮 `aria-label`。
- admin：主导航 JS 同步 `aria-current`、总闸开关按钮 `aria-pressed`+`aria-label`、发布证据按钮 `aria-label`。

### C2-4 `services/web/index.html`、`services/admin/index.html`、`services/web/app.js`

- 两处 toast 容器补 `role="status" aria-live="polite"`。
- web `setLoading` 加载时 `btn.setAttribute('aria-busy','true')`，结束 `removeAttribute`。

### C2-5 `services/web/app.js` + `services/web/styles.css`

- 雷达图：SVG 补 `role="img"`+`aria-label`；每个维度圆点补 `tabindex="0" role="img" aria-label`，并新增 `focus`/`blur` 监听展示悬浮提示（键盘可读），`.radar-point:focus` 加 outline。
- 新增可见「维度列表」`<details class="radar-dims">`（原生可键盘访问），把 28 维的 code/name/分组/分值/说明以文本形式展示，信息不再被 SVG 独占。
- A/B 盲听切换为原生 `<input type="checkbox">`，天然可 Tab+Space 访问（无需改动）。

## 验收命令结果（本机全绿）

```bash
sh -n scripts/acceptance-all.sh                                    # PASS（exit 0）
find services -name '*.js' -print0 | xargs -0 -n1 node --check     # PASS（全绿）
python -m compileall -q services tests scripts                     # PASS
python -m pytest -q tests/unit                                     # 46 passed（基线 46，未减少）
python scripts/architecture-audit.py                              # architecture contracts valid
```

另：`find scripts -name '*.sh' -print0 | xargs -0 -n1 sh -n` 全绿；web/admin `index.html` 标签配平校验 OK；`acceptance-all.sh` 经 mock 成功/失败双路径演练通过。

## aria / role 计数前后对比

统计口径：`aria-` / `role=` **出现次数**（`grep -o … | wc -l`）；括号内为 `grep -c` 命中行数。

| 文件 | aria 前 | aria 后 | role 前 | role 后 |
|---|---|---|---|---|
| services/web/index.html | 1 (1) | 14 (9) | 0 (0) | 6 (2) |
| services/admin/index.html | 0 (0) | 8 (2) | 0 (0) | 1 (1) |
| services/web/app.js | 0 (0) | 15 (12) | 0 (0) | 3 (3) |
| services/admin/admin.js | 0 (0) | 5 (4) | 0 (0) | 0 (0) |
| **合计** | **1 (1)** | **42 (27)** | **0 (0)** | **10 (6)** |

> 结论：`aria-` 出现次数 **1 → 42**、`role=` **0 → 10**，明显上升。

## 遗留 / 风险

1. **Docker E2E 未在本机执行**：本机无 Docker，`acceptance-all.sh` 仅 `sh -n`+mock 演练；真实 E2E 需在部署主机跑（Runbook 已覆盖）。`test.sh` 依赖 `docker compose run` 按依赖自动拉起 api/worker/web/admin（Compose v2 行为），若旧版 Compose 需先 `up`。
2. **admin 无 `<dialog>`**：admin 使用原生 `confirm()/prompt()`（浏览器自带焦点/键盘可用），故 C2-2 的 dialog 焦点管理与 `aria-busy` 不适用，已注明。
3. **容量 Gate 端口释放**：脚本在 capacity 前 `down` 商业/主栈，属编排层判断；若 `KEEP_CAPACITY_STACK=1` 需留意容量栈持续占用端口。
4. **雷达点焦点提示**：键盘 `focus` 触发的悬浮提示按元素 `getBoundingClientRect` 定位，视觉上不如鼠标 hover 精准，但已有可见「维度列表」兜底保证信息可达。
