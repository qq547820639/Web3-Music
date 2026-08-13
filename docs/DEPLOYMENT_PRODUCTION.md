# 生产部署蓝图

本仓库的 Docker Compose 是本地一键启动和集成验证环境。生产部署应保持领域合同，但替换基础设施。

## 推荐拓扑

- Web/Admin：CDN + WAF + 静态托管；
- API：多实例容器平台，OIDC，自动扩缩；
- Worker：独立伸缩池，按 Provider/队列分组；
- PostgreSQL：托管高可用、PITR、只读副本；
- Redis：托管高可用，仅事件/缓存；
- Object Storage：私有 Bucket、SSE-KMS、版本控制和跨区复制；
- Secrets：KMS/Vault/Secret Manager；
- Metrics/Logs/Trace：OpenTelemetry + 托管 Observability；
- Provider/Payment：私网出口、固定 Egress、Webhook Gateway；
- CI/CD：签名镜像、Migration Gate、Canary 和自动回滚。

## 环境分层

- Local：Emulator，无真实资金或权利；
- Dev：共享开发 Provider，数据可重置；
- Staging：与生产拓扑同构，测试账户和合成商业能力；
- Production：正式合同、真实支付、严格审批；
- Disaster Recovery：异区恢复能力和定期演练。

## SLO 建议

- API 可用性 99.9%；
- Quote P95 < 500ms（不含模型）；
- 账本/退款正确率 100%；
- 任务最终恢复率 100%；
- Media Hash 覆盖率 100%；
- 跨租户泄露 0；
- 重复扣费 0；
- Rights Unknown 商业交付 0。

## 发布步骤

1. Schema Compatibility 和 Migration Dry Run；
2. 静态、单元、Contract、Integration、E2E、Chaos、Load 和 Security；
3. Provider/Payment 健康与合同 Snapshot；
4. 备份与回滚点；
5. Staging Smoke；
6. Release Evidence 全部 passed/waived；
7. Canary；
8. 指标观察；
9. 全量；
10. 发布后对账。

## 500 在线用户的低成本起步方式

优先使用 `infrastructure/kubernetes/resonance-capacity-500.yaml` 覆盖基础 Deployment。API 从 2 Pod 起步并 HPA 到 8；Worker 从 2 Pod 起步，每 Pod 默认并行 8 个 Job。生产媒体启用 `MEDIA_DELIVERY_MODE=presigned` 并配置浏览器可访问的对象存储端点，避免音频流量穿过 API。上线前在 Staging 执行 `./scripts/capacity-gate-500.sh`。
