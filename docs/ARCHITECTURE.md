# v13 Final 最终产品架构与领域不变量

## 1. 产品与服务拓扑

```text
Browser
  ├── Creator Studio ──JWT/Workspace──┐
  └── Admin Console ───JWT/Workspace──┤
                                      v
                                  FastAPI API
                         ┌─────────────┼──────────────┐
                         │             │              │
                  PostgreSQL/RLS   Redis Stream   MinIO Private
                         ^             ^              ^
                         │             │              │
                    Lease Worker ─ Outbox ─ Media Ingest
                         │
                         ├── Music Provider Adapter → Emulator / approved provider
                         └── Cost reconciliation → Credit Hold / Ledger

Payment Emulator / Gateway → signed webhook → Payment Inbox → Order fulfillment
Prometheus ← API metrics + Worker metrics
```

PostgreSQL 是任务、账本、资产、权利、订单和审计的事实源；Redis 只承担可重放事件分发和非关键速率限制，不承担财务或任务正确性。

## 2. 信任边界

- 浏览器中的 User ID、Workspace ID、Role、费用、权利声明和 Provider 状态均不可信。
- JWT 只证明用户身份；API 必须从数据库解析 Workspace Membership 和角色。
- `music_app` 受强制 RLS 限制；`music_worker` 仅用于内部跨租户领取任务，所有写入仍携带 Workspace ID。
- Provider 的 URL、MIME、文件名、状态、成本、候选数量和权利说明均不可信。
- Payment Webhook 和 Provider Webhook 必须通过 HMAC，且通过 Inbox 唯一键去重。
- AI 输出只是 Proposal，必须经过 JSON Schema、Revision、锁定、权限和政策校验。
- `unknown` 不是允许；缺少证据时系统必须阻止商业能力。

## 3. Creation OS 不变量

### SongSpec 与 Revision

- Revision 创建后不可 UPDATE/DELETE。
- 每个 Patch 必须声明 `base_revision`；落后版本返回 409。
- JSON Pointer 的父路径和子路径锁定都能阻止修改。
- AI Patch 与手动 Patch 使用同一领域校验器。
- 生成任务永远绑定固定 `project_id + spec_revision`。
- 用户编辑、锁定、选择和评论进入贡献/事件记录。

### 质量引擎

- 同一规则版本与同一输入必须得到相同结果。
- 输出恰好 28 个维度，并记录规则版本、原始证据、风险和建议。
- v2.1-Final 的 PAC、TSMI、NSRQ、C3AC、G_struct、SoftPenalty、CriticalPenalty 与 TEE_offset 由确定性代码实现。
- 引擎分数是决策辅助，不代表版权、商业权利或用户满意度。

### Quote 与 Generation Job

- Quote 绑定固定 Revision、Provider Capability Snapshot、候选数、单价、总 Credits、场景、过期时间和哈希。
- 一个 Quote 只能建立一个 Credit Hold 和一个 Generation Job。
- 同一 Workspace 的 Idempotency-Key：相同请求返回原 Job，不同请求返回 409。
- 没有有效 Quote、用户确认或足够 Credits 时不能提交任务。

## 4. Worker 与 Provider 不变量

- Worker 使用 `FOR UPDATE SKIP LOCKED` 原子领取任务。
- 只有有效 Lease 的 Worker 可以修改执行状态和完成结算。
- Heartbeat 延长 Lease；Worker 崩溃后由下一 Worker恢复。
- Provider Submit 使用平台 Job ID 作为幂等键；已有 `provider_job_id` 时恢复 Poll，不盲目重提。
- 重试使用指数退避；超过上限进入 Dead Letter 并释放未结算 Hold。
- 取消流程为 `cancel_requested → provider cancel → hold release → cancelled`。
- Provider Snapshot 在 Quote 时冻结，历史任务不读取当前配置解释结果。

## 5. 账本与支付不变量

- 每笔 Ledger Transaction 的 Entry 总和必须为零。
- `operation_key` 在 Workspace 内唯一。
- Ledger Transaction 和 Entry 不可修改或删除。
- 每个任务只结算自己的 Credit Hold。
- `settled + released <= original`；终态必须完全闭合。
- 部分成功只按 Ready Candidate 结算，其余释放。
- 支付与退款分别使用请求哈希和幂等键。
- Refund 不能超过剩余可退金额。
- 退款购买 Credits 时，按累计比例撤回 Credits；余额不足则拒绝，避免负余额和无资金退款。
- License 全额退款会撤销 Delivery、释放 Reservation、改变 License 状态并逆转 Payout。

## 6. 媒体不变量

- Provider Clip 不能直接成为 Asset。
- 每次重定向都重新验证协议、Host、DNS 和目标 IP。
- 外部媒体只允许 HTTPS 和公共 IP；本地 Emulator 通过显式 Host Allowlist。
- 流式下载有文件大小限制，并检查 Content-Type、真实音频解码、时长和 SHA-256。
- Ready Candidate 必须有平台私有副本、哈希、大小、MIME 和扫描状态。
- 用户无法枚举 Bucket；播放通过短期、绑定 Workspace 和 Media ID 的 Token。

## 7. Asset OS 与 Rights 不变量

- 只有 Ready Candidate 可以成为 Master。
- 同一 Candidate 不能重复创建 Asset Snapshot。
- Asset Snapshot 与 Rights Manifest 不可原地修改。
- Rights Manifest 逐项返回 `stream/download/share/commercial_use/license/sublicense/distribute/mint/content_id`。
- 每项只有 `allowed/blocked/manual_review/unknown`；只有 `allowed` 才能执行对应商业操作。
- Emulator、`development_only`、`unapproved` 或 `unknown` Provider 无法通过人工审核获得商业能力。
- Legal Hold 或开放 Moderation Case 会阻止 Offer、License 和交付。
- 商业 Offer 必须绑定最新 Rights Manifest；权利版本变化后旧 Offer 失效。

## 8. Market OS 不变量

- Order、Payment、Refund、License、Delivery、Payout 和 Dispute/Support 是独立对象。
- 独家 Offer 在购买时建立有过期时间的 Reservation；其他买方不可重复购买。
- Payment 成功后才创建/激活 License 和 Delivery。
- License 绑定不可变 Rights Manifest、Asset Snapshot、Terms Snapshot 与 License Hash。
- 收入拆分合计必须为 10,000 basis points。
- Payout 经济字段不可变，只允许受控状态迁移；记录不可删除。
- 平台 Payout 操作只能通过平台管理员的 Security Definer 函数，并进入 Audit Event。

## 9. 一致性模式

- API/Worker 在领域状态事务内写 Transactional Outbox。
- Publisher 至少一次发布到 Redis Stream；Consumer 必须按 Event ID 去重。
- Provider/Payment 回调先写 Inbox，再执行业务；重复事件不重复结算。
- 跨数据库和对象存储的本地备份通过暂停 API/Worker 获得协调快照。

## 10. 运行形态

本地最终参考产品共 **15** 个 Compose 服务，`docker compose up` 默认起 **12** 个：PostgreSQL、Redis、MinIO、Migration、Music Emulator（`provider-emulator`）、Payment Emulator、Gateway、API、Worker、Prometheus、Web、Admin；另 3 个只在指定 profile 时起——Acceptance（`--profile test`）、ClamAV（`--profile scan`）、Worker B（`--profile contention`）。这句的三个数都由 `tests/unit/test_reference_doc_figures.py` 从 `docker-compose.yml` 现算核对，改服务要同时改这句。生产部署应替换为托管高可用基础设施，并保持相同领域合同和发布门禁。
