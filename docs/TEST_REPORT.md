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

- 权威运行（2026-09-26）：`scripts/acceptance-all.sh`，全新数据库（起栈前 `down -v`），**19 步 PASS + 1 步按开关跳过（共 20 行）**，commit `b79e70b`，2026-09-26T02:44:13Z → 2026-09-26T02:55:36Z，逐步日志与 compose 日志见 `release-evidence/acceptance-20260926T024413Z/`，浏览器结果为 `release-evidence/browser-a11y-20260926T025229Z/report.json`。步序由 18 增至 20：第 12 步 `mfa-drill`、第 13 步 `member-drill` 新入。上一轮权威运行（`2c3ef7f`，17 步 PASS + 1 跳过，`release-evidence/acceptance-20260925T234750Z/`）作为历史读数保留。可复现性：此前同一主机已有 7 次 `FAIL=0`（`82f2ffe`, `5028686`, `28deafc`, `1d8534e`, `01e61d1`, `2c3ef7f`, `96d5955`，旧→新，当时分别 15/15/16/16/17/18/19 行——套件每轮都在变宽，共同命题是「每次跑完当时全部行」而不是「同样 20 行绿了 7 次」）。发现记录同样保留，本轮两条都是改坏东西的真实捕获：`acceptance-20260926T021711Z` 停在第 3 步——验收套件用自己那份 `PROVIDER_WEBHOOK_SECRET` 字面量签名，而 acceptance 容器从没被给过这个变量（见下条第 11 项）；`acceptance-20260926T022643Z` 停在第 17 步 98/100——两个候选的原因被记成读不出的 `{'type': 'ReadError', 'message': ''}`（见下条第 12 项）。上一轮的两条（`acceptance-20260925T223656Z` 第 1 步红 = 清单写早了；`acceptance-20260925T223811Z` 第 15 步 87/100 红 = 主机 I/O 停摆并暴露「终态失败不带原因」）不动。
- 单元测试：28 → 54 → 66 → 70 → 78 → 114 → **176**（本轮 +62：第二因子原语 22 条含 18 条 RFC 6238 公开向量与一份独立 stdlib 实现并排、成员写入静态守卫 7 条（每条配必开对照）、签名密钥策略 16 条、环境契约 9 条（三种语法、两向求差、白名单每项都要仍为真）、媒体入库重试与错误签名 7 条、导出口径 +1；`pytest -q tests/unit` 无需起栈，2-3 秒）。
- 跨容器常驻用例：默认栈 11 项（备份恢复后复跑再次通过）；商业覆层 11 项（1 项商业全链路 + 10 项跨租户隔离）。
- 第二因子演练：`scripts/mfa_drill.py` **52/52**（流水线第 12 步）。码由 `e2e_client.totp_code()` 现场生成——那是从 RFC 现写的一份 stdlib 实现，与被测服务端用的 pyotp 不同源，否则「绿」只说明两边犯了同一个错。断言里最有分量的四条：待用凭据不能当访问令牌用（反向也一样）、已用过的码换一个待用凭据再放进来仍被拒（重放闸按账号存在库里而不是按进程）、注册时另一条已开会话不会跟着被放行、换种子后旧恢复码在存储的 HMAC 集合里已经不存在。
- 成员与角色演练：`scripts/member_drill.py` **33/33**（流水线第 13 步）。其中两条以 `music_app` 身份真发 `INSERT` / 自升 `UPDATE` 并要求数据库回答 permission denied，一条以伪造 actor 直接调 017 的函数，一条把「唯一属主 → 删除被拒 → 移交 → 删除成功」这条曾经走不通的链跑完。
- 部署侧签名密钥：`docker-compose.yml` 的 `${JWT_SECRET:-字面量}` 类兜底全部改为 `${VAR:?说明}`，`DEMO_STACK` 以字面量在基础文件钉 `true`、生产覆层显式 `false`，不为真时 API 启动即拒绝（实测输出 `refusing to start: jwt_secret is still a literal published in this repository | ...`）。`.env.example` 里 `JWT_SECRET` 与 `MEDIA_SIGNING_SECRET` 原本同为一个 `change-me-before-sharing`，已按用途拆成四个不同演示值。
- 契约测试 4 → **5** 项（新增 vendor 方言被接受 + 违约体被拒两例）；Worker Kill-9 恢复通过；双 Worker Lease 竞争通过（8 任务 / 2 claimant / 160 credits 结算一次）。
- 备份恢复：绝对指纹校验通过（本轮读数 `2 workspaces, 2 assets byte-identical, ledgers unchanged`，计数随库里资产数变化，判据含零分母拒绝），并已用「删除 master 对象 → 判据转红 → 恢复 → 转绿」证明该校验有牙。
- 100 次生成回归：`100/100 completed, error rate 0.0%`，两次流水线读数分别为 p50 6.25s / p95 8.31s 与 p50 8.53s / p95 13.64s、各 1000 credits 结算（计时随主机负载浮动，按次引用），逐任务校验哈希、结算额与账本守恒。
- **真实浏览器验收（第 19 步）**：Playwright + axe-core 4.13.0，桌面 1440 与手机 390 两档共 70 个视图记录 / 44 次 axe 扫描，**critical 0 / serious 0**（moderate 64 项列为后续项：`region` 40、`heading-order` 18、`landmark-one-main` 6），并附 CSP、键盘可达、390px 横向溢出与非捕获异常断言——报告里 `uncaught_errors` 为空，而 16 条 console 留痕全部是匿名首屏那次预期内的 401，两者不是一回事，判据只对前者判红（该留痕已作为附带发现登记，见 `RELEASE_CHECKLIST.md` 附带发现第 4 条）；机器可读结果 `release-evidence/browser-a11y-20260926T025229Z/report.json`。判据自身有牙：`--self-test` 三类注入对照 + 12 条判决函数单测。
- **数据主体访问与删除演练（新增）**：`scripts/erasure_drill.py` 驱动 `GET /api/account/export` 与 `POST /api/account/erasure`，**46 项断言**（流水线内为第 11 步，权威运行读数 46/46；独立运行亦 46/46。上一版本文记 32、本段此前记 34，两处都不再作数——同一个数在两份文档里各自漂移，正是这轮把它改成从 `information_schema` 现推的原因）。每条守卫都拍两极：确认词不匹配→409 且库里一行未动；唯一属主自删→409 且会话未被撤销；平台管理员→409 且演示属主随后仍能登录；匹配确认→会话被撤销且 `expires_at` 一字节未动、成员关系与偏好消失、身份列被假名化、再登录 401。保留性用「真去删一次、要求数据库拒绝」来证（实录 `ERROR: song_spec_revisions is immutable`）；覆盖性用 `information_schema` 里所有指向 `users(id)` 的外键列与导出自身声明的 `coverage` 求差来证，并做过正向对照（临时建 `tmp_probe_link(user_id REFERENCES users(id))` → 判据精确点名并判红，删表后转绿）。演练夹具按设计留 residue：被删除账户的假名化溯源行是 append-only，删不掉正是被测性质本身。
- **第三方 Provider 适配器往返（第 18 步）**：`docker-compose.generic-rest.yml` 把 `MUSIC_PROVIDER` 切成 `generic_rest`，让 `GenericRESTAdapter` 而不是自家模拟器适配器驱动生成；`provider_regression.py` 新增第三个参数，先看 `/api/bootstrap` 报出的身份再干活——覆层没生效就直接判红（实测：关掉覆层时报 `the stack reports 'emulator' ... the overlay did not take effect`），把端点指向死主机时报 `job_error={'type': 'ConnectError', ...}`（改前只会打 `error=None`）。该步已经跑过三次，三次都是 `25/25 completed, error rate 0.0%, settled 250 credits`，p50/p95 依次为 5.21s/6.37s（`2c3ef7f`，第 16 步）、4.14s/4.19s（`96d5955`，第 17 步）、4.14s/4.18s（本轮，第 18 步）——计数会跨跑传，耗时不会。诚实边界：端点仍是我们的模拟器，这一步证明的是适配器与契约管线，不等于真实 Provider，G9 那条仍然未闭。
- 执行中实测到并修复的真实缺陷（详表见 `docs/FINAL_RELEASE_STATUS.md` 与 `docs/RELEASE_CHECKLIST.md` 的"附带发现"）：独家资产可被重复售出、`GET /orders` 把分页行数当金额、退款后独家商品卡在 `sold`、自家 CSP 静默作废雷达配色/提示/进度条、UI 代理把重建后 API 的死地址钉住导致全量 502、错误路径重复读流导致真因被吞、管理后台工作区下拉无可访问名、390px 视口被顶栏撑到 435px、入库交付清单 `SOURCE_MANIFEST.sha256` 只有 110/188 个文件被如实描述（现由 `scripts/source-manifest.sh check` 进 `static-verify` 把关），以及本轮新增的三条导出/删除侧缺陷：导出把 `brand_submissions` 的租户列当作 `workspace_id` 导致整份导出 500；删除函数撞上 005 自家会话不可变触发器（`expires_at` 在不可变清单里），于是每一次删除都 409；`user_preferences`/`product_events` 受 FORCE RLS 保护，不带租户作用域的读静默返回空——同一 RLS 陷阱在写侧是 011/012，在读侧此前无人踩过；终态失败可以完全不带原因（13/15 个 failed 作业 `error=NULL`，真因藏在候选行里，需手工 join 才看得到）；以及两个 provider 适配器各自发明一种请求方言而仓库里没有任何写下来的 provider 契约（`generic_rest` 对我们自己的模拟器次次 422）。

仍未取得有效证据的只有一项：**500 并发容量 Gate**。本机 4 vCPU / 6 GiB 低于 `docker-compose.capacity500.yml` 自身对 api/worker 各 4 CPU / 4 GiB 的请求，Gate 在 500 用户下实测判红（p95 4197ms 与 9832ms 两次，错误率 0%），并发扫描与结论见 `docs/COST_OPTIMIZED_500_CONCURRENCY.md`。因此上面那句「需部署主机的运行日志才算证据」依然成立——只是现在有了真实的失败读数，而不是缺失读数。
