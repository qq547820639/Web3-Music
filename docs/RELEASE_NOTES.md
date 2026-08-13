# v13 Final Product 发布说明

发布日期：2026-08-07

## 定位

v13 在此前的生产候选内核扩展为 Creation OS、Asset OS、Market OS、Intelligence 和 Trust 五层完整参考产品。它通过统一 Gateway 可本地一键启动，可用于产品验证、封闭测试、供应商合同测试、支付/账本演练、商业许可流程验证和工程交接。

## 主要新增

- 正式 Creator Studio 和运营管理控制面；
- 分支、评论、偏好、产品事件和 AI Registry；
- 28 维质量引擎与 v2.1-Final 数学模型实现；
- Payment Emulator、订单、订阅、退款和账本 Credits；
- Rights Evidence、Legal Review、Moderation 和 Legal Hold；
- 商业 Offer、独家 Reservation、License、Delivery、Brand Brief、付费 Award；
- Revenue Split、Payout Queue 和退款逆转；
- API/Worker Metrics 与 Prometheus；
- Provider/Payment Contract Test、商业 Acceptance、Chaos Worker 恢复；
- 协调备份、校验与恢复脚本；
- OpenAPI v13、JSON Schema、发布门禁和完整运营文档。

## 安全默认值

- 默认音乐 Provider 为 Emulator；商业能力为 `blocked`。
- `suno_community` 为 `development_only`。
- `synthetic_licensed` 仅在商业测试覆盖层启用。
- Payout 全局总闸默认为关闭。
- Public Sharing 默认为关闭。
- Rights `unknown/blocked/manual_review` 不会被 UI 当作允许。

## 兼容性

数据库使用校验和迁移。已运行环境不得修改历史迁移，应新增迁移文件。API 版本为 `13.0.0`；OpenAPI 文件与运行时同步生成。

## 已知边界

- 没有内置真实音乐 Provider 合同或密钥；
- 没有内置真实支付、税务、发票和银行 Payout；
- 本地身份和 Secret 只适合演示；
- 未集成独立反病毒、外部内容审核和生产级 OpenTelemetry；
- Web UI 为功能完整的参考前端，不替代正式设计系统、浏览器矩阵和可访问性认证；
- 本构建环境未执行 Docker 跨容器 E2E，相关测试由随包 Compose 和 CI 执行。

## v13 最终强化

- HttpOnly Access/Refresh Cookie、服务器端 Session Hash、轮换、撤销与 CSRF。
- 统一 Gateway、同源 Studio/Admin/API、限流与安全响应头。
- 生成前确定性 Policy Preflight 与不可变 Moderation Decision。
- Contract-first Generic REST Provider Adapter。
- G13 发布证据与生产部署参考。
