# v13 实施与维护说明

## 1. 数据库迁移

`services/migrate/migrate.py` 按文件名执行 `db/migrations/*.sql`，并把 SHA-256 记录到 `schema_migrations`。已执行迁移内容发生变化时启动失败，防止静默篡改历史。

数据库角色：

- `music_admin`：仅迁移和恢复；
- `music_app`：API，强制 RLS；
- `music_worker`：内部 Worker，可跨 Workspace 领取任务，但凭证不对外暴露。

`001` 提供生产候选领域内核，`002` 提供 Creation/Asset/Market OS，`003` 增加跨租户外键、全局 License Template 可见性和 Payout 状态约束。

## 2. API 和前端

API 的 OpenAPI 事实源位于：

- `shared/contracts/openapi-v13.json`
- `shared/contracts/openapi-v13.yaml`

运行中的 API 可重新导出：

```bash
make openapi
```

Web 与 Admin 通过同源 Nginx 代理访问 `/api` 和 `/media`，避免在浏览器中硬编码后端地址。Nginx 设置 CSP、Frame Deny、Referrer Policy 和 MIME Sniffing 防护。

## 3. 任务执行与恢复

领取任务：

```sql
SELECT id
FROM generation_jobs
WHERE ...
ORDER BY next_attempt_at,created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

领取后写入 Lease Owner、Lease Expiry、Heartbeat 和 Attempt Count。正在执行的 Worker 周期性续租；崩溃后下一 Worker 使用已保存的 Provider Job ID 恢复。

可重试错误：网络/读取超时、429、5xx、Provider 未完成、Lease 失效。达到 `max_attempts` 后进入 `dead_letter`，并释放未结算 Hold。

## 4. Provider Adapter

统一方法：

```text
get_capabilities
quote
submit
get_status
cancel
health_check
reconcile_cost
```

默认 Emulator 支持轮询式成功、部分成功、失败、超时、限流和取消场景。`suno_community` 只用于开发，硬编码为不可商业授权。新增真实 Provider 时必须遵守 `docs/PROVIDER_ADAPTER_CONTRACT.md`。

## 5. 质量引擎

`services/api/app/domain/quality.py` 将附件方法论实现为确定性参考规则：

- Styles V1–V14；
- Lyrics V15–V28；
- 普通话 465 分、粤语 470 分；
- PAC、TSMI、NSRQ、C3AC；
- TEE 归一化、结构 Gate、SoftPenalty、CriticalPenalty；
- 风险和意象同质化提示。

原始方法论副本保存在 `docs/reference/quality-engine-v2.1-final-source.md`。规则变化应新增版本并进行 Golden Dataset 重放，不应覆盖历史结果。

## 6. DeepSeek Orchestrator

模型请求返回结构化 Proposal：`command/base_revision/operations/preserve/reason`。API 会再次验证 JSON、Revision、锁定路径、允许的 Patch 操作和业务权限。模型不可直接执行 Provider、账本、Master、Rights 或 License 命令。

生产使用应增加 Prompt/Model 灰度、Token/Cost 记录、PII 脱敏、空输出/截断重试和 Offline Eval。

## 7. 支付与商业流程

Payment Emulator 使用 SQLite 保存 Intent、Refund、Request Hash 和 Idempotency Key，并发送 HMAC 回调。生产 Gateway Adapter 必须保持相同幂等与金额校验语义。

Order 履约：

- Credit Pack：支付成功后向 Ledger 发行 Credits；
- Subscription：创建 30 天参考 Subscription；
- License：激活 License，创建 Delivery，确认独家 Reservation，创建 85/15 收入拆分与 Seller Payout。

商业流程覆盖层仅用于端到端测试，不代表真实 Provider、支付或税务授权。

## 8. 可观测性

API 暴露 `/metrics`；Worker 在 `:9101/metrics` 暴露任务、候选、重试和 Outbox 指标。Prometheus 本地服务位于 `:9090`。

生产应增加 OpenTelemetry Trace、集中结构化日志、告警路由和长期 Metrics 存储。Trace ID 应贯穿 Quote、Hold、Job、Provider、Media、Settlement、Asset 和 Order。

## 9. 备份恢复

本地：

```bash
./scripts/backup.sh <name>
./scripts/verify-backup.sh backups/<name>
RESTORE_CONFIRM=YES ./scripts/restore.sh backups/<name>
```

脚本停止 API/Worker，导出 PostgreSQL Custom Dump，复制 MinIO 数据，生成 SHA-256 清单，然后恢复写入服务。恢复会清空本地 MinIO 数据卷并恢复备份内容。

生产必须使用数据库 PITR、对象版本控制、跨区复制、加密备份和定期恢复演练。

## 10. CI/CD

GitHub Actions 分三条链：

1. Static/Unit：Python、JavaScript、Shell、Compose、JSON/Schema/OpenAPI、架构审计与单元测试；
2. Default Compose：完整验收、Provider/Payment Contract、Worker Kill-9 恢复、备份恢复后重验；
3. Commercial Flow：显式合成商业 Provider 覆盖层，验证 Offer、Reservation、Payment、License、Delivery、Payout 与 Refund Reversal。

生产流水线还应增加 SAST、DAST、依赖漏洞扫描、SBOM、镜像签名、Migration Dry Run、Load Test 和 Staging Gate。
