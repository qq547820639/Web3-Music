# 安全模型与部署责任

## 已实施

- JWT Bearer Authentication、PBKDF2-SHA256 密码存储；
- Workspace Membership、RBAC、平台管理员与租户管理员分离；
- PostgreSQL 强制 Row-Level Security 和同 Workspace 复合外键；
- 限定 CORS Origin、CSP、Frame Deny、Referrer Policy、MIME Sniffing 防护；
- 登录速率限制与 Request ID；
- 私有对象存储、短期 HMAC Media Token；
- Provider/Payment Webhook HMAC 与 Inbox 去重；
- SSRF、DNS/IP、重定向、大小、MIME、音频解码和 SHA-256 校验；
- Revision、Ledger、Asset、Rights、Revenue Split 和 Payout 不可变/受控触发器；
- API/Worker Prometheus 指标；
- 关键业务和管理员操作 Audit Event；
- 平台总闸、Legal Hold、内容案件和发布证据室。

## 本地演示中刻意保留的边界

- JWT 存于浏览器 Local Storage；生产推荐 HttpOnly、Secure、SameSite Cookie 或企业 IdP；
- 默认密码、JWT Secret、Webhook Secret 和 MinIO 凭证是演示值；
- Provider/Payment Emulator 不处理真实凭证和资金；
- 未集成 CSAM 检测与商业内容审核；反病毒是一个**可插拔的边界**而不是默认能力：`media_assets.scan_status` 只允许 `unscanned`/`clean` 两态（`db/migrations/020_media_scan_truth.sql` 的 CHECK 与 `media_assets_scan_proof` 触发器），`clean` 必须带引擎自报名与扫描时刻，值只能来自 `services/worker/media_scan.py` 对引擎应答的白名单判读（只有 `stream(...) OK` 算干净）；引擎不可达时作业重试、判定为脏时字节不落桶。这台部署没配引擎就老实记 `unscanned`——本轮之前它无条件记 `clean`，而那一格同时是下载接口的判据；
- Prometheus 没有认证，仅适合本地网络。

## 对外部署前强制要求

- OIDC/SSO、MFA、账户锁定、密码重置、Session Revoke 和 Key Rotation；
- TLS、WAF、DDoS 防护、用户/IP/Workspace 级限流；（应用内已有账号维度、来源地址维度与投诉收件两道固定窗口，见 `docs/RELEASE_CHECKLIST.md`「已修」第 24 条；来源维度信的是网关自己看到的对端，信任表在 `docker-compose.yml:120` 写死成网关的静态地址，因此部署时若换网段必须同时改这一格。Workspace 级配额、TLS、WAF、DDoS 仍属部署侧，代码内没有 substitute 之物）
- Vault/KMS/Secret Manager 和短期数据库凭证；
- 托管 PostgreSQL/Redis/Object Storage，静态与传输加密；
- SAST、DAST、依赖与容器扫描、SBOM、镜像签名和 Provenance；
- 独立恶意文件扫描、内容安全与人工复核；
- SIEM、异常消费/登录检测、审计保留策略；
- 多租户渗透测试、支付与账本专项审计；
- 正式隐私政策、数据处理协议、删除/导出流程和地区合规；
- 经过演练的 RPO/RTO 和事故响应联系人。

严禁在漏洞报告中发送真实 Cookie、API Key、未公开音频、支付信息或个人数据。应提供最小复现、受影响版本和预期/实际行为。
