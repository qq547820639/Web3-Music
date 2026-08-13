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
| compose 起栈 → acceptance(test.sh) → contract → chaos → backup → restore → 复跑 acceptance | `compose-acceptance` |
| commercial-flow（commercial-test 覆层） | `commercial-flow` |
| capacity-gate-500（可选） | 无 CI 等价，本地容量验证 |

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
| 1 | static-verify | 编译校验（Python/JS/sh）+ JSON/OpenAPI/JSON Schema 校验 + `architecture-audit.py` + `pytest -q tests/unit` | 退出码 0，无 `missing required contracts` |
| 2 | unit-test-docker | `scripts/test.sh`：`build acceptance` + `run --rm acceptance`（`run` 按依赖自动拉起 api/worker/web/admin） | 退出码 0 |
| 3 | compose-up | `docker compose up --build -d` + `docker compose ps` | 全部服务 `Up`/`healthy` |
| 4 | acceptance | `docker compose --profile test run --rm acceptance` | 退出码 0，pytest 全过 |
| 5 | contract-test | `scripts/contract-test.sh`（provider/payment 契约测试） | 退出码 0 |
| 6 | chaos-worker-recovery | 杀 worker → 等租约到期 → 拉起 → 校验补偿/恢复 | 退出码 0，`verify` 通过 |
| 7 | backup-restore | `backup.sh acceptance` → `RESTORE_CONFIRM=YES restore.sh backups/acceptance` | 备份校验通过、恢复完成 |
| 8 | acceptance-rerun | 复跑默认 acceptance（验证恢复后的数据一致性） | 退出码 0 |
| 9 | commercial-flow | commercial-test 覆层起栈 + `acceptance-commercial`（`test_commercial.py`） | 退出码 0 |
| 10 | capacity-gate-500 | 仅 `CAPACITY=1` 时跑；否则 SKIPPED | 错误率 ≤ 1%、p95 ≤ 800ms、随后 acceptance 通过 |

**判读要点**：每步结束后控制台会打印 `STEP N RESULT: PASS/FAIL`。任一步 `FAIL` 会立即停止，并提示 `该步骤失败，日志在 …`。

---

## 5. Go / No-Go 清单

| 门禁 | 判定标准 | Go | No-Go |
|---|---|---|---|
| 静态校验 | `static-verify.sh` 退出码 0 | ✅ | ❌ |
| 单元测试 | `pytest -q tests/unit` 全过（基线 46，不得少于 46） | ✅ | ❌ |
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
| 步骤 3 起栈失败，端口冲突 | 8080/8000/8010/8020/54329/63799/9000/9090 等被占用 | `lsof` 定位占用进程，释放后重跑 |
| 镜像拉取失败 / 构建超时 | 网络不通、镜像源不可达、磁盘不足 | 配置镜像加速、`docker system df` 检查磁盘、重试 |
| 步骤 4/8 账本差异或断言失败 | 前序步骤残留脏数据、`restore` 后未等待健康 | 确认 §4 顺序执行；`docker compose ps` 看 healthcheck；必要时 `scripts/reset.sh` 清栈后重跑 |
| 步骤 8（复跑 acceptance）额度泄漏/余额不符 | 恢复点与测试数据不一致 | 确认步骤 7 使用 `backups/acceptance`；检查 `SUMMARY.txt` 时间线是否连续 |
| 步骤 6 chaos 超时 | worker 租约等待不够 | 调大 `LEASE_WAIT_SECONDS`（默认 35）后单独重跑 `scripts/chaos-worker-recovery.sh` |
| 步骤 9 商业闭环失败 | Provider 未处于 `approved_commercial` | 确认 commercial-test 覆层已应用（脚本已自动加 `-f`）；检查 `commercial-compose-logs.txt` |
| 步骤 10 容量 Gate 失败 | 资源不足 / Provider 限流 | 提高 CPU/RAM，调大 `CAPACITY_MAX_P95_MS`，或 `KEEP_CAPACITY_STACK=1` 保留现场排查 |

---

## 8. 收尾

```bash
# 验收完成后清理整套栈（可选，视后续用途）
docker compose down --remove-orphans
```

> 不要用 `down -v` 清理，除非你确定要删除 Postgres/MinIO 数据卷（`-v` 会清空本地验收数据）。
