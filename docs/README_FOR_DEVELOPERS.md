# Resonance · 面向开发者 / 运维的技术说明

> 本文件面向**开发者与运维人员**，承接原 `README.md` 中的技术内容（部署、验证、安全边界、目录结构、容量说明等）。面向终端用户的产品介绍请回到根目录 [`README.md`](../README.md)。

Resonance 是一套可通过 Docker Compose 一键启动的完整参考产品，覆盖 AI 音乐创作、质量评估、真实异步生成适配、媒体入库、双分录额度账本、资产证据、权利能力、订单支付、商业许可、品牌任务和运营控制面。

默认配置完全自包含：音乐 Provider Emulator 生成测试 WAV，Payment Emulator 模拟支付与退款。它可以用于本地体验、产品验收、故障演练、Provider/Payment Contract Test 和工程交接。Emulator 输出不具有商业音乐权利，系统会强制将商业能力标记为 `blocked`。

## 一键启动

```bash
cp .env.example .env
docker compose up --build
```

统一入口：

| 功能 | 地址 |
|---|---|
| Creator Studio | http://localhost:8080 |
| Admin 控制台 | http://localhost:8080/admin/ |
| API 文档 | http://localhost:8080/docs |
| API 健康检查 | http://localhost:8080/health |
| Provider Emulator | http://localhost:8010/docs |
| Payment Emulator | http://localhost:8020/docs |
| Prometheus | http://localhost:9090 |
| MinIO Console | http://localhost:9001 |

调试时也可直接访问 Studio `http://localhost:4173`、Admin `http://localhost:4174` 和 API `http://localhost:8000/docs`。

本地账户：

| 身份 | 邮箱 | 密码 |
|---|---|---|
| 平台管理员 / Workspace Owner | `owner@example.local` | `demo-owner` |
| Creator | `creator@example.local` | `demo-creator` |
| 另一租户 Owner | `viewer@other.local` | `demo-viewer` |

## 产品闭环

```text
登录与 Workspace Membership
→ SongProject / 不可变 SongSpecRevision
→ 对话 Patch、手动编辑、字段锁定、分支与评论
→ 28 维质量评估与 v2.1-Final TEE 模型
→ 确定性内容与权利预检
→ 固定 Revision、Provider Snapshot、费用与权利摘要的 Quote
→ 独立 Credit Hold
→ PostgreSQL Lease Worker、Heartbeat、Retry、Dead Letter
→ Provider Emulator / Generic REST Provider Adapter
→ SSRF 防护、媒体解码、SHA-256、MinIO 私有入库
→ 成功、部分成功、失败结算与额度释放
→ A/B 试听与 Master Selection
→ Asset Snapshot、Rights Manifest、证据与 Legal Hold
→ Offer、Order、Payment、License、Delivery
→ Brand Brief、Submission、Award、Revenue Split、Payout
→ Refund 时撤销交付、释放独家预留并逆转分账
```

## v13 最终强化

- 浏览器默认使用 HttpOnly Access/Refresh Cookie，不再把令牌保存到 `localStorage`。
- Refresh Token 服务器端哈希存储、轮换与即时撤销。
- 所有 Cookie 写操作需要双提交 CSRF Token；Bearer API 客户端保持兼容。
- 统一 Nginx Gateway 提供同源 Studio、Admin、API 和媒体访问，并加入限流与安全响应头。
- 生成报价前执行版本化、可解释的确定性 Policy Preflight；高风险声音克隆请求被阻断，艺人风格和第三方素材引用进入人工复核。
- 新增 `generic_rest` Contract-first Provider Adapter，支持正式供应商的能力、提交、轮询、取消、幂等和成本回传。
- Provider 合同版本、审批状态和能力快照写入历史任务，不使用当前配置解释过去资产。
- G13 发布证据室跟踪会话撤销、Policy Preflight、统一 Gateway、Provider Contract、Docker E2E 与恢复证据。

## 自动验证

```bash
./scripts/static-verify.sh
./scripts/test.sh
./scripts/contract-test.sh
```

宿主机侧脚本（`browser_a11y.py` 与各演练）默认用字面地址 `http://127.0.0.1:<port>` 而不是
`localhost`：这台开发机是双栈的，`localhost` 会优先解析到 `::1`，而 `::1` 上的 4173 可能被**另一个项目**
的 dev/preview server 占走（2026-09-26 真发生过，见 `FINAL_RELEASE_STATUS.md` 同日一节）。
浏览器门禁在走任何状态之前会先取一次本站首屏，与镜像逐字 COPY 的 `services/web/index.html` /
`services/admin/index.html` 做 sha256 比对，不符就带着对方的 `<title>` 退出——这条既防端口被抢，
也防"镜像不是从被认证的树构建的"。要指向别处仍然用 `WEB_URL` / `ADMIN_URL` / `API_BASE_URL` 覆盖，
只是覆盖值请写 IP 或写你确认过的名字。

商业许可合成测试：

```bash
docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml \
  --profile commercial-test run --rm acceptance-commercial
```

Worker 恢复演练：

```bash
./scripts/chaos-worker-recovery.sh
```

发布证据包：

```bash
./scripts/release-evidence.sh
```

## 文书读数的落地方式：为什么是自己盖章而不是引入现成工具

六份发布文书（`docs/RELEASE_CHECKLIST.md`、`docs/TEST_REPORT.md`、`docs/FINAL_RELEASE_STATUS.md`、
`docs/CODE_WALKTHROUGH.md`、`docs/E2E_ACCEPTANCE_RUNBOOK.md`、`docs/CHANGELOG_COST500.md`）里的每个运行读数，
现在都由 `scripts/release_face_cells.py` 按「标记行 + 正则 + 模板」就地改写；这套做法是在比过两条现成路线之后
留下的选择，写在这里免得后来者以为只是顺手造的轮子。

- 候选一，构建期转叙：mdBook 的 `{{#include file.rs:anchor}}`（`https://rust-lang.github.io/mdBook/format/mdbook.html`，
  MIT，主线活跃）支持按行号与具名锚点把外部片段插进章节。功能匹配度：能解决"数字来自别处"，但它插入的是整段
  外部文本，要保住中文句子周围的上下文就得把每句话拆成独立片段文件；License：MIT 无冲突；维护与质量：无问题；
  安全：本地渲染，无新增面；适配成本：要给 192 行纯 Markdown 的文档集引入一本书的构建工具链，而本仓的文档
  按 GitHub 直接读、不出站点。**结论：借语义不引依赖**——我们的"标记行"就是它的具名锚点思路，
  正则命中不为 1 就整轮拒写，这一点是它的锚点模式没有的判决语义。
- 候选二，报告直出：`pytest-json-report` 1.5.0（`https://pypi.org/project/pytest-json-report/`，MIT）把
  session 时间、环境元数据、逐用例耗时与结果写成 JSON，供下游自动消费；其页面对"把数字写进人读的文档"
  没有任何机制。本仓已经在用它的一条坑：`environment` 字段在 pytest-metadata ≥3 下恒为空对象，
  所以它连元数据都不可靠。**结论：只用它做机读侧，不做文书侧**。
- 留下的自研部分共 1242 行 Python——`scripts/stamp_release_faces.py`（479 行）、`scripts/release_face_cells.py`（707 行）与 `scripts/metric_line.py`（127 行）——只做三件事：从在册件读数（`stamp_release_faces.figures()`）、
  按格改写（`release_face_cells.apply()`，任一格解析不到就整体不落盘）、以及把"格要的数没人算"与
  "算出来的数没人引用"两个方向都钉成常驻用例（`tests/unit/test_release_face_cells.py`）。
  替换任一现成方案都要先把"取不到读数即整轮拒写"这条判决语义重新实现一遍——这是本仓文书唯一不能退化的性质。

## 正式 Provider

生产接入使用 `generic_rest`：

```env
MUSIC_PROVIDER=generic_rest
PROVIDER_APPROVAL_STATUS=approved_commercial
PROVIDER_CONTRACT_VERSION=contract-2026-001
GENERIC_PROVIDER_BASE_URL=https://provider.example.com
GENERIC_PROVIDER_API_KEY=...
GENERIC_PROVIDER_MODEL=...
```

外部服务需实现 `docs/PROVIDER_ADAPTER_CONTRACT.md` 定义的异步任务合同。正式上线前必须通过 `services/acceptance/test_provider_contract.py`，并提供书面合同、输出权利、成本和数据处理证据。

`SunoCommunityAdapter` 仅保留为开发兼容层，系统不会让它获得商业能力。

## 安全边界

- JWT 只证明用户身份；每个请求仍从数据库解析 Workspace Membership。
- 会话不等于授权：不可逆或会削权的自助动作（删除账户、移交工作区所有权、改/删成员、关闭两步验证）在**发起那一次请求里**再交一次口令，已注册第二因子的账号同时要给一个当前验证码。缺凭证答 401（挑战，不计入爆破窗口），凭证错答 403（计入，每账号每分钟六次，答对即清零）；刻意不设确认窗口——Laravel 那类"确认一次三小时不再问"的会话戳，正是被偷走的 Cookie 会走的通道。哪些端点带这道检查由 `scripts/authority_matrix.py` 从端点函数体派生，写在 `docs/AUTHORITY_MATRIX.md` 的「再认证」列。
- 「谁能调用哪个端点」不靠记忆：`./scripts/authority_matrix.py --write` 从代码派生 `docs/AUTHORITY_MATRIX.md`，`--check`（已接进 `static-verify.sh`）会重算并比对；改了某个端点的角色名单却忘了改文档，红的是构建。
- PostgreSQL RLS 为多租户隔离提供第二层保护。
- AI 只能提出 Patch，不能扣费、调用 Provider、选定 Master、修改 Rights Manifest 或签发 License。
- Ledger Transaction 与 Entry 追加保存，余额只能由分录聚合。
- 每个 Job 只能结算自己的 Credit Hold。
- Provider URL、状态、MIME、文件名、成本和回调均不可信。
- `unknown`、`blocked` 或 `manual_review` 不能在 UI 中被当作商业许可。
- 媒体只存入私有对象存储，播放使用短期签名 Token。
- 主体权利是自助通道而不是工单：`GET /api/account/export` 逐列声明读了什么、刻意不给什么及理由，`POST /api/account/erasure` 只在数据库内比对账户邮箱后假名化身份并切断访问，账务与溯源按保留义务留存（`db/migrations/013`/`014`/`016`）。两条都有界面入口，且被浏览器验收当场走完（含一次真实的自我删除）。
- 第二因子种子只以 AESGCM 密封态入库，`MFA_ENCRYPTION_KEY` 在代码里没有默认值（未配置返回 503 而不是 500）；重放闸按账号存在数据库里，不是按 worker 进程存在内存里。

## 生产部署前的外部条件

代码包不能替代真实经营主体、供应商合同或合规审批。对外收费前仍需完成：正式 OIDC/SSO 对接（平台自带的 TOTP 第二步验证已实现并由常驻演练覆盖，企业 SSO 需要外部 IdP）、KMS/Secret Manager、TLS/WAF、托管高可用 PostgreSQL/Redis/对象存储、集中日志与 Trace、病毒扫描与专业内容审核、真实支付机构、税务与发票、正式音乐 Provider 合同、至少 100 次真实生成回归、独立渗透测试和灾难恢复演练。

## 目录

```text
services/api                 FastAPI、身份会话、领域 API、质量、权利、账本与市场
services/worker              Lease Worker、Provider、媒体入库、结算与 Outbox
services/provider-emulator   持久化音乐供应商故障实验室
services/payment-emulator    支付、回调与退款模拟器
services/web                 Creator Studio
services/admin               运营、财务、信任与发布控制面
services/gateway             统一同源入口、限流与安全响应头
services/migrate             带 SHA-256 的版本化迁移器
services/acceptance          默认、商业、Provider 与 Payment 验收
shared/contracts             OpenAPI v13 与 JSON Schema 事实源
db/migrations                领域状态、RLS、约束和不可变规则
infrastructure               Prometheus 与生产部署参考
scripts                      启动、验证、Chaos、备份恢复和发布证据
```

## 500 并发低成本生产化包

本交付增加了面向约 500 名同时在线用户的低成本容量强化：PostgreSQL 连接池与背压、Worker 单进程并行、Provider/媒体 HTTP 连接复用、对象存储直出、容量索引、Kubernetes HPA/PDB 和一键容量 Gate。

在有 Docker 的预发布主机上运行：

```bash
./scripts/capacity-gate-500.sh
```

详细参数与生产规格见 `docs/COST_OPTIMIZED_500_CONCURRENCY.md`。该 Gate 验证 500 个并发用户访问平台 API；500 个音乐生成任务同时执行属于 Provider 容量/合同测试，不能用用户并发测试替代。

## 文档索引

- [ARCHITECTURE.md](./ARCHITECTURE.md) — 系统架构
- [RUNBOOK.md](./RUNBOOK.md) — 运维手册
- [PROVIDER_ADAPTER_CONTRACT.md](./PROVIDER_ADAPTER_CONTRACT.md) — 正式供应商异步任务合同
- [RIGHTS_POLICY.md](./RIGHTS_POLICY.md) — 权利策略
- [DEPLOYMENT_PRODUCTION.md](./DEPLOYMENT_PRODUCTION.md) — 生产部署
- [COST_OPTIMIZED_500_CONCURRENCY.md](./COST_OPTIMIZED_500_CONCURRENCY.md) — 500 并发低成本容量规格
- [CODE_WALKTHROUGH.md](./CODE_WALKTHROUGH.md) — 代码走读
- [COMMERCIAL_OPERATIONS.md](./COMMERCIAL_OPERATIONS.md) — 商业运营
- [DATA_AND_AI_GOVERNANCE.md](./DATA_AND_AI_GOVERNANCE.md) — 数据与 AI 治理
- [IMPLEMENTATION.md](./IMPLEMENTATION.md) — 实现说明
- [TEST_REPORT.md](./TEST_REPORT.md) — 测试报告
- [RELEASE_NOTES.md](./RELEASE_NOTES.md) — 发布说明
- [RELEASE_CHECKLIST.md](./RELEASE_CHECKLIST.md) — 发布清单
- [FINAL_PRODUCT_SCOPE.md](./FINAL_PRODUCT_SCOPE.md) — 最终产品范围
- [FINAL_RELEASE_STATUS.md](./FINAL_RELEASE_STATUS.md) — 最终发布状态
- [CHANGELOG_COST500.md](./CHANGELOG_COST500.md) — 成本优化变更记录
- [ITERATION_CHANGES.md](./ITERATION_CHANGES.md) / [ITERATION_CHANGES_PHASE2.md](./ITERATION_CHANGES_PHASE2.md) / [ITERATION_PLAN_PHASE2.md](./ITERATION_PLAN_PHASE2.md) — 迭代变更与计划
- [TRACEABILITY_MATRIX.csv](./TRACEABILITY_MATRIX.csv) — 追溯矩阵
