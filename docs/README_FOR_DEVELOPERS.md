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
- PostgreSQL RLS 为多租户隔离提供第二层保护。
- AI 只能提出 Patch，不能扣费、调用 Provider、选定 Master、修改 Rights Manifest 或签发 License。
- Ledger Transaction 与 Entry 追加保存，余额只能由分录聚合。
- 每个 Job 只能结算自己的 Credit Hold。
- Provider URL、状态、MIME、文件名、成本和回调均不可信。
- `unknown`、`blocked` 或 `manual_review` 不能在 UI 中被当作商业许可。
- 媒体只存入私有对象存储，播放使用短期签名 Token。

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
