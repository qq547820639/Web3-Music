# E2E 验收 Runbook（部署主机 SOP）

> 适用版本：Resonance AI 音乐资产平台 v13 · 入口脚本：`scripts/acceptance-all.sh`
> 受众：不熟悉本代码库的运维/发布人员。照本文件逐步执行即可产出可归档的验收证据包。

---

## 1. 这是什么

`scripts/acceptance-all.sh` 把平台的全部端到端（E2E）验收动作串成**一条命令**，按固定顺序执行，任一步失败即停并告诉你日志在哪，全部通过后把步骤摘要与 `docker compose logs` 归档到 `release-evidence/acceptance-<时间戳>/`。

它执行的序列等价于 CI（`.github/workflows/ci.yml`，每次 push 自动运行，无需本地 Docker）：

| 本脚本步骤 | 等价 CI job |
|---|---|
| static-verify（含 `pytest tests/unit`） | `static-and-unit` |
| compose-up → acceptance → contract-test → chaos → lease-contention → restore-fidelity-snapshot → backup-restore → restore-fidelity-compare → acceptance-rerun → erasure/mfa/member/media-scan/report 五支演练 → generic-rest-roundtrip | `compose-acceptance` |
| commercial-flow（commercial-test 覆层）+ market-reconciliation + reservation-race + hold-drill | `commercial-flow` |
| browser-a11y（`BROWSER=1` 才在本地跑） | `browser-a11y`（CI 每次都跑） |
| capacity-gate-500（可选） | `capacity-500` |

> 「链上跑的脚本 CI 也跑」这句话现在有机检：`tests/unit/test_ci_covers_chain_payloads.py` 取 `acceptance-all.sh` 里所有 `scripts/*.py|*.sh` 载荷与 `ci.yml` 的载荷做差集，多一个就红——本轮 `media_scan_drill.py` 与 `restore_fidelity.py` 正是这条差集查出来的（前者有第 14 步却没有 CI 作业，后者只在本地量过）。唯一的豁免是 `test.sh`：CI 直接执行它的载荷 `docker compose --profile test run --rm acceptance`，豁免理由由同一条测试复核。

> 💡 若本机没有 Docker，直接 `git push` 到仓库即可让 GitHub Actions 替你执行同一套 E2E，结果在仓库 **Actions** 页查看，日志以 artifacts 留存（`compose-logs` / `commercial-compose-logs` / `release-evidence`）。

---

## 2. 前置条件

### 2.1 软件

| 依赖 | 最低版本 | 检查命令 |
|---|---|---|
| Docker Engine | 24.x | `docker version` |
| Docker Compose | **v2.x**（`docker compose` 子命令，非 `docker-compose`） | `docker compose version` |
| Bash | 3.2+（脚本 shebang 为 bash） | `bash --version` |
| GNU `date` / `tr` / `tee` / `cat` | 任意现代版本 | `date -u +%Y%m%dT%H%M%SZ` |
| Python（**跑在宿主上**，不是容器里） | 3.10+（PEP 604 语法）+ `pytest` | `python -V`（注意是 `python` 而不是 `python3`） |
| Node.js | 任意能 `--check` ES2020 的版本 | `node --version` |

> **第 1 步与五支演练在宿主的 Python 里跑，不在容器里**：`scripts/static-verify.sh` 直接执行
> `python -m compileall`、`node --check`、`sh -n`、`scripts/authority_matrix.py --check` 与
> `pytest -q tests/unit`，`python scripts/*_drill.py` 同理。本机没装全局 `python`，这条链用的是
> `/Users/panhao/.venvs/w3m-e2e`（3.12.13），依赖 = `services/api/requirements.txt`
> （fastapi 0.116.1 / httpx 0.28.1 / jsonschema 4.25.0 / redis 6.4.0 / pyotp 2.10.0 / segno 1.6.6 …）
> \+ `scripts/requirements-drill.txt`（zxing-cpp 3.1.1）\+ `scripts/requirements-browser.txt`
> （playwright 1.63.0）\+ `pytest`。运行前必须把它的 bin 放到 PATH 前面：
> `export PATH="/Users/panhao/.venvs/w3m-e2e/bin:$PATH"`；否则 `acceptance-all.sh:29-33` 的预检（清单
> 是 `python node docker sha256sum xargs`）以 `预检失败：PATH 上没有可执行的 python。…` 退出码 2 立刻停下
> ——**这条红是跑错 shell，不是被测代码**。本轮两种错法都实测过：不设 PATH 时 `python` 不存在，第 1 步不会
> 开始；退而用系统 `python3`（3.9）或 `~/.local/bin/pytest`（9.1.1，其解释器没装 fastapi）时，
> `pytest -q tests/unit` 在**收集阶段**就停（前者 10 个 `TypeError: unsupported operand type(s) for |`
> 加 `psycopg2.pool` 缺失，后者 16 个 `ModuleNotFoundError: No module named 'fastapi'`），一条用例都没跑到，
> 而输出长得像套件坏了。

> 脚本全程 POSIX-sh 语法，`sh -n` 与 `bash -n` 均可通过，但运行时请用 bash（`./scripts/acceptance-all.sh`）。

### 2.2 资源规格（单机参考栈）

| 项目 | 建议 |
|---|---|
| CPU | ≥ 4 核（跑 capacity gate 建议 ≥ 8 核） |
| 内存 | ≥ 8 GB（跑 capacity gate 建议 ≥ 16 GB） |
| 磁盘 | ≥ 20 GB 可用（镜像 + Postgres/MinIO 数据卷） |

### 2.3 端口占用检查

脚本会绑定以下宿主机端口，先确认没被其它进程占用：

```
8080  网关 gateway
8000  API
8010  Provider 模拟器
8020  Payment 模拟器
4173  Web（开发容器，非宿主入口）
4174  Admin（开发容器）
54329 Postgres
63799 Redis
9000/9001  MinIO API / Console
9090  Prometheus
```

检查命令（逐个确认无监听即可）：

```bash
for p in 8080 8000 8010 8020 4173 4174 54329 63799 9000 9001 9090; do
  lsof -iTCP:$p -sTCP:LISTEN -n -P 2>/dev/null || echo "port $p free"
done
```

### 2.4 `.env` 准备

```bash
test -f .env || cp .env.example .env
```

> 本地验收走 Provider/Payment 模拟器，`.env.example` 的默认值即可。**生产环境必须替换所有 `change-me-*` 密钥**（脚本本身不校验密钥强度，密钥安全由发布清单把关）。

---

## 3. 完整分步执行

### 3.1 干跑（可选，先确认脚本可解析）

```bash
sh -n scripts/acceptance-all.sh
```

### 3.2 一键执行（默认，跳过容量 Gate）

```bash
./scripts/acceptance-all.sh
```

### 3.3 含容量 Gate（耗时更长，依赖 Provider）

```bash
CAPACITY=1 ./scripts/acceptance-all.sh
```

容量 Gate 可用环境变量微调（默认值见 `scripts/capacity-gate-500.sh`）：
`CAPACITY_USERS`（默认 500）、`CAPACITY_REQUESTS_PER_USER`（2）、`CAPACITY_MAX_ERROR_RATE`（1%）、`CAPACITY_MAX_P95_MS`（800ms）、`KEEP_CAPACITY_STACK`（1 则跑完不销毁容量栈）。

---

## 4. 每步做什么 / 结果怎么判读

| # | 步骤名 | 实际执行 | 通过判据 |
|---|---|---|---|
| 1 | static-verify | `scripts/static-verify.sh`：`python -m compileall` + `node --check` + `sh -n` + `scripts/source-manifest.sh check` + `scripts/authority_matrix.py --check` + Compose/JSON/JSON Schema/OpenAPI 校验 + `scripts/architecture-audit.py` + `pytest -q tests/unit` | 退出码 0；`architecture-audit.py` 打印 `architecture contracts valid`，末行是 `pytest -q tests/unit` 的结果 |
| 2 | compose-up | `scripts/acceptance-all.sh` 的 `step_stack_up`：`docker compose --profile '*' config --format json` 取带 `build` 段的服务，逐个 `docker compose --profile '*' build "$service"`（串行，任一失败即整步失败），再 `docker compose up -d`、`docker compose ps` | 退出码 0；`docker compose ps` 全部服务状态为 `Up`/`healthy` |
| 3 | acceptance | `scripts/test.sh`：`docker compose --profile test build acceptance` + `docker compose --profile test run --rm acceptance` | 退出码 0，pytest 全过 |
| 4 | contract-test | `scripts/contract-test.sh`：`docker compose --profile test run --rm acceptance pytest -q test_provider_contract.py test_payment_contract.py` | 退出码 0 |
| 5 | chaos-worker-recovery | `scripts/chaos-worker-recovery.sh`：`chaos_worker_recovery.py submit` → `docker compose kill -s KILL worker` → `sleep "${LEASE_WAIT_SECONDS:-35}"` → `docker compose up -d worker` → `chaos_worker_recovery.py verify` | 退出码 0，`verify` 通过：过期租约被重启的 worker 收回、恢复作业取消、额度无泄漏 |
| 6 | lease-contention | `scripts/lease-contention.sh`：`docker compose --profile contention up -d worker worker-b` → 双跑者镜像预检 → `lease_contention.py prepare` → `lease_contention.py verify` | 预检先行：宿主 `services/worker/worker.py` 与 `worker`、`worker-b` 容器内 `/app/worker.py` 的 sha256 必须两两相等，各打一行 `contention preflight:`；不等即 `LEASE CONTENTION FAIL: <name> runs ...` 退出 1——**这一行红说明比的是两个构建，而不是竞态失败**。之后 `lease contention passed` 要求本轮至少两个不同 `lease_owner` 真实认领、作业不重复、额度只结算一次 |
| 7 | restore-fidelity-snapshot | `python scripts/restore_fidelity.py snapshot` | 退出码 0 且打印 `restore-fidelity snapshot written: N assets ...`；覆盖 0 资产或台账为空即失败 |
| 8 | backup-restore | `scripts/backup.sh acceptance` → `RESTORE_CONFIRM=YES ./scripts/restore.sh backups/acceptance`（restore 内部先跑 `scripts/verify-backup.sh`） | 备份清单校验通过、`Restore complete`、栈重新 `docker compose up -d`，并且**经由网关的 `/health` 必须回到 200**——端口向 `docker compose port gateway 80` 现取。这一步会重建应用层容器，容器一重建 IPAM 就给新地址；`acceptance-20260928T054641Z` 就是因为网关还在拨旧地址而整轮 502，而网关自己的 healthcheck 问本机路径，所以它一路「健康」 |
| 9 | restore-fidelity-compare | `python scripts/restore_fidelity.py compare` | 打印 `restore fidelity passed: ... byte-identical, ledgers unchanged`；任何资产字节或台账余额漂移打 `RESTORE FIDELITY FAIL` 并退出 1 |
| 10 | acceptance-rerun | `docker compose --profile test run --rm acceptance` | 退出码 0（验证恢复后的数据一致性） |
| 11 | erasure-drill | `python scripts/erasure_drill.py` | 退出码 0，`erasure drill: N/N checks passed` |
| 12 | mfa-drill | `python scripts/mfa_drill.py` | 退出码 0，`mfa drill: N/N checks passed` |
| 13 | member-drill | `python scripts/member_drill.py` | 退出码 0，`member drill: N/N checks passed` |
| 14 | media-scan-drill | `python scripts/media_scan_drill.py` | 退出码 0，`media scan drill: N/N checks passed`（伪造判决被 DB 拒、诚实 `unscanned` 资产仍可听、且无残留行） |
| 15 | report-drill | `python scripts/report_drill.py` | 退出码 0，`report drill: N/N checks passed`（三种 subject 的响应逐字段相同、举报邮箱不进租户可见的那张单子、建成后的通知改不动也删不掉、投递窗口按地址与全站各一只且拒绝一次不花费） |
| 16 | commercial-flow | `docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml up --build -d` → 同覆层 `--profile commercial-test run --rm acceptance-commercial` | 退出码 0 |
| 17 | reservation-race | `python scripts/reservation_race.py`（需要步骤 16 的商业覆层仍在跑） | 打印 `reservation race passed: ... exactly one active licence` |
| 18 | market-reconciliation | `python scripts/reconcile_market.py` | 打印 `market reconciliation: N/N checks passed`（licence/交付/退款/分账四表对账与 85/15 分账策略一致） |
| 19 | hold-drill | `python scripts/hold_drill.py`（需要步骤 16 的商业覆层仍在跑） | 打印 `legal hold drill: N/N checks passed`；两种载体各自的拒绝措辞、放行后的同一条调用必须开火与不开火、目录里读得到该标记的对象恰好那三个、以及 `media_assets` 没有删除触发器这一条实测缺口 |
| 20 | provider-regression-100 | `python scripts/provider_regression.py "${REGRESSION_JOBS:-100}" "${REGRESSION_CONCURRENCY:-8}"` | 打印 `provider regression: N/N completed, error rate ...` 且无 `FAIL:` 行；台账闭合、无悬挂 hold |
| 21 | generic-rest-roundtrip | `docker compose -f docker-compose.yml -f docker-compose.generic-rest.yml up --build -d` → `python scripts/provider_regression.py "${GENERIC_REST_JOBS:-25}" 4 generic_rest` | 退出码 0；`/api/bootstrap` 报出的 provider 身份必须是 `generic_rest`，否则 `expected provider ...` 直接判红 |
| 22 | browser-a11y | 默认**不执行**，`SUMMARY.txt` 记 `SKIPPED (BROWSER=1 才执行)`；`BROWSER=1` 时 `python scripts/browser_a11y.py --self-test` → `python scripts/browser_a11y.py` | 退出码 0，`browser a11y + walkthrough passed`；前置为 playwright + Chromium（`scripts/requirements-browser.txt`） |
| 23 | capacity-gate-500 | 默认**不执行**，`SUMMARY.txt` 记 `SKIPPED (CAPACITY=1 才执行)`；`CAPACITY=1` 时先 `docker compose down --remove-orphans`（商业覆层同做一次）释放端口，再 `./scripts/capacity-gate-500.sh` | 错误率 ≤ `CAPACITY_MAX_ERROR_RATE`（默认 1%）、p95 ≤ `CAPACITY_MAX_P95_MS`（默认 800ms），随后容量栈里的 acceptance 复跑通过 |

**判读要点**：每步结束后控制台会打印 `STEP N RESULT: PASS/FAIL`。任一步 `FAIL` 会立即停止，并提示 `该步骤失败，日志在 …`。

---

## 5. Go / No-Go 清单

| 门禁 | 判定标准 | Go | No-Go |
|---|---|---|---|
| 静态校验 | `static-verify.sh` 退出码 0 | ✅ | ❌ |
| 单元测试 | `pytest -q tests/unit` 全过。用例数由 `pytest -q tests/unit --collect-only -q` 现读（本轮实测 423 个收集实例，含参数化展开），门禁判的是退出码 0、且数量不得比上一轮少 | ✅ | ❌ |
| 默认 E2E | `acceptance`（步骤 4）全过 | ✅ | ❌ |
| 契约测试 | `contract-test.sh` 全过 | ✅ | ❌ |
| 故障恢复 | `chaos-worker-recovery.sh` verify 通过 | ✅ | ❌ |
| 备份/恢复 | `verify-backup.sh` 通过 + 恢复后复跑 acceptance 全过 | ✅ | ❌ |
| 商业闭环 | `acceptance-commercial` 全过 | ✅ | ❌ |
| 容量 Gate（可选） | 若启用：错误率 ≤ 1% 且 p95 ≤ 800ms 且随后 acceptance 全过 | ✅ | ❌ |

**结论**：以上必选门禁（1–9）全绿 = **Go**；任一步失败 = **No-Go**，按 §7 排查后重跑。容量 Gate 为可选增强项，未启用不影响必选门禁的 Go/No-Go。

---

## 6. 证据收集与归档

脚本自动完成，无需手工：

- 目录：`release-evidence/acceptance-<YYYYmmddTHHMMSSZ>/`
- 内容：
  - `SUMMARY.txt` — 每步 PASS/FAIL + 起止时间戳
  - `step-N-<name>.log` — 每步完整输出
  - `compose-logs.txt` — 主栈 `docker compose logs --no-color`
  - `commercial-compose-logs.txt` — 商业覆层日志

发布时把该目录（或打包后的 tar）随版本归档。可参考既有 `scripts/release-evidence.sh` 的 `environment.txt / source.sha256 / sbom-lite.json` 补强证据链。

---

## 7. 常见失败排查

| 现象 | 可能原因 | 处置 |
|---|---|---|
| 步骤 2 起栈失败，端口冲突 | 8080/8000/8010/8020/54329/63799/9000/9090 等被占用（`build` 阶段不占端口，冲突发生在随后的 `docker compose up -d`） | `lsof` 定位占用进程，释放后重跑 |
| 镜像拉取失败 / 构建超时 | 网络不通、镜像源不可达、磁盘不足 | 配置镜像加速、`docker system df` 检查磁盘、重试 |
| 步骤 3/10 账本差异或断言失败 | 前序步骤残留脏数据、`restore` 后未等待健康 | 确认 §4 顺序执行；`docker compose ps` 看 healthcheck；必要时 `scripts/reset.sh` 清栈后重跑 |
| 步骤 10（复跑 acceptance）额度泄漏/余额不符 | 恢复点与测试数据不一致 | 确认步骤 8 使用 `backups/acceptance`；检查 `SUMMARY.txt` 时间线是否连续 |
| 步骤 5 chaos 超时 | worker 租约等待不够 | 调大 `LEASE_WAIT_SECONDS`（默认 35）后单独重跑 `scripts/chaos-worker-recovery.sh` |
| 步骤 16 商业闭环失败 | Provider 未处于 `approved_commercial` | 确认 commercial-test 覆层已应用（脚本已自动加 `-f`）；检查 `commercial-compose-logs.txt` |
| 步骤 22 容量 Gate 失败 | 资源不足 / Provider 限流 | 提高 CPU/RAM，调大 `CAPACITY_MAX_P95_MS`，或 `KEEP_CAPACITY_STACK=1` 保留现场排查 |

---

## 8. 收尾

```bash
# 验收完成后清理整套栈（可选，视后续用途）
docker compose down --remove-orphans
```

> 不要用 `down -v` 清理，除非你确定要删除 Postgres/MinIO 数据卷（`-v` 会清空本地验收数据）。
