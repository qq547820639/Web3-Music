# v13 运行、事故与恢复 Runbook

## 启动与检查

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
curl -fsS http://localhost:8010/health
curl -fsS http://localhost:8020/health
curl -fsS http://localhost:9090/-/ready
```

日志：

```bash
docker compose logs -f api worker provider-emulator payment-emulator
```

## 生成任务卡住

1. 查看 Job 的 `status/lease_owner/lease_expires_at/heartbeat_at/attempt_count/provider_job_id`。
2. Lease 未过期时先看 Worker 日志，避免人工并发处理。
3. Worker 已退出：`docker compose up -d worker`。
4. Lease 到期后新 Worker 自动恢复；已有 Provider Job ID 时继续 Poll。
5. `dead_letter` 终态会释放未结算 Hold。重新生成必须创建新 Quote/Job，不复活旧财务交易。
6. 禁止手工修改 `settled_credits`、Hold 或余额。

## Provider 事故

- 关闭新任务：管理后台将 `provider_enabled=false` 或 `generation_enabled=false`。
- 已提交任务保留 Provider Job ID；恢复后继续 Poll。
- Provider 回调重复不会重复处理。
- Provider 能力/条款变化必须创建新 Capability Snapshot，不能改写历史任务。
- 权利不确定时关闭 `licenses_enabled` 和 `marketplace_enabled`。

## Payment 事故

- 关闭 `payments_enabled`，保留订单和已收到 Inbox。
- Provider 请求超时且结果不确定时，使用同一 Idempotency-Key 查询/重试，不创建新 Payment。
- Webhook 金额、币种、Payment ID 或 Order ID 不匹配时拒绝处理并升级 P1。
- 禁止直接修改订单为 `fulfilled`；只允许经过已验证 Payment Event。

## 紧急总闸

- `generation_enabled`：新 Quote/Job；
- `provider_enabled`：新 Provider Submit；
- `exports_enabled`：资产/交付导出；
- `payments_enabled`：支付；
- `marketplace_enabled`：市场；
- `licenses_enabled`：许可；
- `brand_market_enabled`：品牌任务；
- `payouts_enabled`：Payout；
- `public_sharing_enabled`：公开分享。

只有平台管理员可以改变全局总闸，操作进入 Audit Event。

## 账本争议

检查：`credit_holds`、`ledger_transactions`、`ledger_entries`、`generation_jobs`、`payments`、`refunds`。

必须满足：

```text
Ledger entries sum = 0
Hold.settled + Hold.released <= Hold.original
终态 Hold.settled + Hold.released = Hold.original
Job.settled_credits = Hold.settled
Payment.refunded_amount <= Payment.amount
```

任何人工补偿都应新增幂等 Adjustment/Reversal，不得 UPDATE 历史账本。

## 权利或投诉事故

1. 创建 Moderation/Policy Case；
2. 必要时 `legal_hold=true`；
3. 暂停 Offer、License、Export 和 Public Sharing；
4. 保存投诉、证据、条款版本和处理人；
5. 不删除相关 Asset、Rights、Audit 和 Ledger；
6. 复核后创建新 Rights Manifest，不覆盖旧版本。

## 备份和恢复

```bash
./scripts/backup.sh incident-$(date -u +%Y%m%dT%H%M%SZ)
./scripts/verify-backup.sh backups/<name>
RESTORE_CONFIRM=YES ./scripts/restore.sh backups/<name>
```

恢复后必须运行 Acceptance，并核对：Ledger Net、Credit Hold、Asset Media Hash、Rights Manifest、Payment/Refund 和 Delivery Package。

## 事故等级

- **P0**：跨租户泄露、重复扣费、错误商业授权、账本不平、密钥泄露；立即关闭相关总闸。
- **P1**：生成大面积失败、支付回调异常、媒体不可用、恢复失败；30 分钟内响应。
- **P2**：单用户任务、UI、延迟或非关键数据问题；当日处理。

事故结束后必须记录时间线、影响范围、根因、补偿、永久修复和回归测试。
