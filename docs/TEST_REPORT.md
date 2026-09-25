# v13 Final 最终产品验证报告

生成日期：2026-08-07

## 本交付环境实际执行

| 验证 | 结果 |
|---|---|
| Python `compileall`（services/tests/scripts） | 通过 |
| Creator Studio 与 Admin JavaScript `node --check` | 通过 |
| Shell 脚本 `sh -n` | 通过 |
| 基础与商业 Compose YAML 解析 | 通过 |
| 所有 JSON 解析 | 通过 |
| JSON Schema 元模式校验 | 通过 |
| OpenAPI JSON/YAML 一致性 | 通过 |
| 架构与禁用旧模式审计 | 通过 |
| 单元测试 | 17/17 通过 |
| 28 维质量引擎确定性与 v2.1 数学关系 | 通过 |
| 普通话/粤语歌词量表 | 通过 |
| 父子 JSON Pointer 锁定 | 通过 |
| Media Token 绑定与过期 | 通过 |
| Emulator Rights 商业阻断 | 通过 |
| Payment Intent/Refund 幂等与超额退款阻断 | 通过 |
| Provider Emulator 幂等、冲突、部分成功与 WAV | 通过 |
| API `/health` 与 Prometheus Middleware | 通过 |
| OpenAPI | v13.0.0，69 条路径 |
| Compose 服务定义 | 12 个服务 |

实际静态命令：

```bash
./scripts/static-verify.sh
```

实际结果：

```text
Compose, JSON, JSON Schema and OpenAPI contracts valid
architecture contracts valid
17 passed
```

## 随包提供、但未在本构建容器执行的 Docker 验收

本构建环境没有 Docker/Podman 守护进程和 PostgreSQL 服务，因此没有虚构以下跨容器结果：

- PostgreSQL Migration 与真实 RLS；
- Postgres Lease Worker、Heartbeat 和 Kill-9 恢复；
- MinIO 私有媒体入库；
- Redis Outbox；
- Payment Webhook 跨容器回调；
- 备份恢复后的完整重验；
- 合成商业许可端到端链路。

这些测试已经编码到 Compose Acceptance 和 GitHub Actions。安装 Docker 的主机运行：

```bash
cp .env.example .env
docker compose up --build -d
docker compose --profile test run --rm acceptance
./scripts/contract-test.sh
./scripts/chaos-worker-recovery.sh
```

商业闭环：

```bash
docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml \
  --profile commercial-test run --rm acceptance-commercial
```

## 默认 Acceptance 覆盖

- JWT、Membership、RBAC、平台管理员边界和跨租户 404；
- Web/Admin 页面和 API/Worker Metrics；
- Revision、锁定、AI/手动 Patch、质量重放；
- Quote 固定 Revision/Provider Snapshot/费用；
- Job 幂等、冲突、成功、部分成功、失败、超时与取消；
- 独立 Hold、结算、释放与账本余额；
- 私有媒体 Token、音频下载和 SHA-256；
- Master、Asset Snapshot、Rights Manifest 与个人资产导出；
- Webhook 签名与重复事件去重；
- 分支、评论、偏好、产品事件和支持工单；
- Credit Order、Payment、Refund、Credits 回收；
- 默认 Provider 无法建立商业 Offer。

## Commercial Acceptance 覆盖

- 显式 `synthetic_licensed + approved_commercial` 测试快照；
- 权利审核后创建独家 Offer；
- 跨 Workspace 购买与 Reservation 隐藏；
- Payment 成功后激活 License 和 Delivery；
- 下载包含音频、License、Rights Manifest 与 Asset Snapshot 的 ZIP；
- 85/15 Revenue Split 和 Seller Payout；
- 全额退款撤销 Delivery、改变 License、释放 Reservation、暂停独家 Offer并逆转 Payout。

## 真实商业上线仍需外部证据

- 正式音乐 Provider 合同与至少 100 次真实 Contract/Cost/Rights 回归；
- 正式支付机构、税务、发票和退款对账；
- 500 并发生成、数据库主从切换、对象存储故障和 DNS Rebinding 专项；
- 独立安全测试、内容审核和版权投诉演练；
- 真实用户满意 Master、留存、退款率和贡献毛利数据。

## 500 并发低成本强化增量验证

本增量交付在当前无 Docker daemon 的交付环境中实际执行：

- Python compile / YAML / JSON / Schema / OpenAPI 静态验证：通过；
- Kubernetes YAML 解析：通过；
- 架构审计：通过；
- 单元测试：28/28 通过；
- 新增 PostgreSQL 连接池、Worker 并行、Provider HTTP 连接复用、presigned 媒体直出、容量索引和容量 Gate 脚本：已完成代码与静态验证。

由于本交付环境没有 Docker daemon，**没有宣称 500 并发已经实测通过**。在部署/预发布 Docker 主机执行 `./scripts/capacity-gate-500.sh` 后，其日志才是 500 并发 Gate 的有效运行证据。

## 2026-09-25 追加：真实执行结果

上文各表是当次打包的静态/单元记录，保留原文不再改写。本轮 Docker 可用，验收已在真实 Compose 栈执行：

- 权威运行：`scripts/acceptance-all.sh`，全新数据库，**15 步 PASS + 1 步按开关跳过**，commit `1d8534e`，2026-09-25T18:39:55Z → 18:48:49Z，逐步日志与 compose 日志见 `release-evidence/acceptance-20260925T183955Z/`（上一提交 `28deafc` 亦全绿，可复现）（此前 14 步的 `82f2ffe` 运行保留为发现记录）。
- 单元测试：28 → 54 → 66 → **70**（新增跨租户、市场对账策略、浏览器验收判决函数、导出口径静态守卫等）。
- 跨容器常驻用例：默认栈 11 项（备份恢复后复跑再次通过）；商业覆层 11 项（1 项商业全链路 + 10 项跨租户隔离）。
- 契约测试 4 项；Worker Kill-9 恢复通过；双 Worker Lease 竞争通过（8 任务 / 2 claimant / 160 credits 结算一次）。
- 备份恢复：绝对指纹校验通过（本轮读数 `2 workspaces, 2 assets byte-identical, ledgers unchanged`，计数随库里资产数变化，判据含零分母拒绝），并已用「删除 master 对象 → 判据转红 → 恢复 → 转绿」证明该校验有牙。
- 100 次生成回归：`100/100 completed, error rate 0.0%`，两次流水线读数分别为 p50 6.25s / p95 8.31s 与 p50 8.53s / p95 13.64s、各 1000 credits 结算（计时随主机负载浮动，按次引用），逐任务校验哈希、结算额与账本守恒。
- **真实浏览器验收（新增，第 15 步）**：Playwright + axe-core 4.13.0，桌面 1440 与手机 390 两档共 62 个视图记录 / 36 次 axe 扫描，**critical 0 / serious 0**（moderate 48 项列为后续项），并附 CSP、键盘可达、390px 横向溢出与非捕获异常断言；机器可读结果 `release-evidence/browser-a11y-20260925T184749Z/report.json`。判据自身有牙：`--self-test` 三类注入对照 + 12 条判决函数单测。
- **数据主体访问与删除演练（新增）**：`scripts/erasure_drill.py` 驱动 `GET /api/account/export` 与 `POST /api/account/erasure`，**34 项断言**（本机独立运行 34/34；它同时是流水线新增的一步，流水线内的读数见本文开头的权威运行指针）。每条守卫都拍两极：确认词不匹配→409 且库里一行未动；唯一属主自删→409 且会话未被撤销；平台管理员→409 且演示属主随后仍能登录；匹配确认→会话被撤销且 `expires_at` 一字节未动、成员关系与偏好消失、身份列被假名化、再登录 401。保留性用「真去删一次、要求数据库拒绝」来证（实录 `ERROR: song_spec_revisions is immutable`）；覆盖性用 `information_schema` 里所有指向 `users(id)` 的外键列与导出自身声明的 `coverage` 求差来证，并做过正向对照（临时建 `tmp_probe_link(user_id REFERENCES users(id))` → 判据精确点名并判红，删表后转绿）。演练夹具按设计留 residue：被删除账户的假名化溯源行是 append-only，删不掉正是被测性质本身。
- 执行中实测到并修复的真实缺陷（详表见 `docs/FINAL_RELEASE_STATUS.md` 与 `docs/RELEASE_CHECKLIST.md` 的"附带发现"）：独家资产可被重复售出、`GET /orders` 把分页行数当金额、退款后独家商品卡在 `sold`、自家 CSP 静默作废雷达配色/提示/进度条、UI 代理把重建后 API 的死地址钉住导致全量 502、错误路径重复读流导致真因被吞、管理后台工作区下拉无可访问名、390px 视口被顶栏撑到 435px、入库交付清单 `SOURCE_MANIFEST.sha256` 只有 110/188 个文件被如实描述（现由 `scripts/source-manifest.sh check` 进 `static-verify` 把关），以及本轮新增的三条导出/删除侧缺陷：导出把 `brand_submissions` 的租户列当作 `workspace_id` 导致整份导出 500；删除函数撞上 005 自家会话不可变触发器（`expires_at` 在不可变清单里），于是每一次删除都 409；`user_preferences`/`product_events` 受 FORCE RLS 保护，不带租户作用域的读静默返回空——同一 RLS 陷阱在写侧是 011/012，在读侧此前无人踩过。

仍未取得有效证据的只有一项：**500 并发容量 Gate**。本机 4 vCPU / 6 GiB 低于 `docker-compose.capacity500.yml` 自身对 api/worker 各 4 CPU / 4 GiB 的请求，Gate 在 500 用户下实测判红（p95 4197ms 与 9832ms 两次，错误率 0%），并发扫描与结论见 `docs/COST_OPTIMIZED_500_CONCURRENCY.md`。因此上面那句「需部署主机的运行日志才算证据」依然成立——只是现在有了真实的失败读数，而不是缺失读数。
