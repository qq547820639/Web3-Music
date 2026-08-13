# 第三阶段实施计划 · 交付可验证性收口

> 日期：2026-08-13 · 团队：软件开发团队（交付总监齐活林编排）
> 依据：`docs/CODE_WALKTHROUGH.md` P0-1 / T01、T07；报告 §5.8 a11y。

## 目标

把两件"上一阶段明确标注为遗留"的事闭环，且都**在本机可完成、可验证**（不依赖 Docker/浏览器）：

| 编号 | 任务 | 类型 | 验证方式 |
|---|---|---|---|
| **C1** | E2E 一键验收脚本 + 部署主机 Runbook | 脚本+文档 | `sh -n` + 结构走查（脚本在 Docker 主机才真正执行） |
| **C2** | 无障碍（a11y）硬化 | 前端 | `node --check` + `aria-*` 计数提升 + 结构走查 |

## C1 · E2E 验收 Runbook + 一键脚本（P0 解锁器）

**背景**：现有 `scripts/`（test/contract-test/chaos-worker-recovery/backup/restore/capacity-gate-500/smoke/static-verify）与 `.github/workflows/ci.yml` 已覆盖 E2E 各环节，但缺一个**串联全流程、产出统一证据包**的入口，以及一份"在部署主机上怎么一步步跑、怎么判 Go/No-Go"的 SOP。

**交付**：
1. `scripts/acceptance-all.sh`：`set -euo pipefail`，按顺序执行——
   static-verify → 单元测试 → compose 起栈 → 默认 acceptance → contract-test → chaos-worker-recovery → backup → restore（带确认）→ 复跑 acceptance → commercial-flow（commercial-test 覆层）→ capacity-gate-500（可选，`CAPACITY=1` 才跑，因耗时/依赖 Provider）。每步打印清晰分节，失败即停并输出诊断提示；全部通过后把结果与 `docker compose logs`、时间戳写入 `release-evidence/acceptance-<ts>/` 证据包（对齐现有 `release-evidence.sh` 的产出规范）。
2. `docs/E2E_ACCEPTANCE_RUNBOOK.md`：部署主机 SOP——前置条件（Docker + Compose v2、资源规格、端口占用、`.env` 配置）、分步执行、结果判读、**Go/No-Go 清单**、证据收集与归档、常见失败排查。语言清晰，可让一个不熟这套代码的运维照着跑通。

## C2 · a11y 硬化（从 demo 到产品的标志）

**背景**：报告 §5.8——前端 `aria-*` 几乎为 0、`<dialog>` 无焦点管理、JS 生成元素无 ARIA、无地标。

**交付**（web + admin 两处，最小变更）：
1. 语义地标：`index.html` 补 `<header>/<nav>/<main>/<footer>`、`lang`、`main` 跳转链接。
2. `<dialog>` 焦点管理：`showDialog`/`askDialog`/`confirmDialog` 打开时焦点移入、关闭时焦点还原（focus trap 至少 Tab 循环在 dialog 内）。
3. 动态元素 ARIA：按钮/输入/`<audio>`/状态指示加 `aria-label`/`aria-pressed`/`role`。
4. 通知无障碍：toast 加 `aria-live="polite"`，加载态加 `aria-busy`。
5. 键盘可用：关键交互可 Tab 到达、Enter/Space 触发（原生 button 已具备，主要补 dialog 与自定义控件）。

## 验收命令（本机，全绿）

```bash
cd /Volumes/Extra/CodeProj/web3_music
sh -n scripts/acceptance-all.sh
find services -name '*.js' -print0 | xargs -0 -n1 node --check
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python -m compileall -q services tests scripts
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python -m pytest -q tests/unit   # 基线 46
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python scripts/architecture-audit.py
```

## 明确不做（本轮）

- 不实际执行 Docker E2E（本机无 Docker，脚本交付后在部署主机跑）。
- 不引入 Vite/React、不做浏览器 axe 扫描（无浏览器；仅静态 a11y 硬化）。
- 不动 admin_v12 控制面重构（P1 后续）。
