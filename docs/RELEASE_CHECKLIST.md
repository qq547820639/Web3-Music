# 发布门禁清单

状态口径（2026-09-25 实测更新）：

- `[x]` = 本轮在真实 Compose 环境执行并通过，证据落盘于 `release-evidence/acceptance-20260925T144245Z/`（commit `82f2ffe`，2026-09-25T14:42:45Z → 14:54:14Z，`acceptance-all.sh` 全 14 步 PASS）。
- `[ ]` = 未通过或**无法在源码环境内完成**，每条都写明缺的是哪一份外部事实。
- 以往"本机无 Docker daemon，需在部署主机执行"的说法已失效：本轮 Docker 可用，跨容器验收已在真实 Compose 栈上跑通，本文按执行结果改写。

## G9 Verified Beta

- [x] Compose E2E 通过；→ `acceptance`/`acceptance-rerun` 两步 PASS，11 项常驻用例在真实 api/worker/web/admin/gateway 栈上通过。
- [x] 两个 Worker Lease 竞争与 Kill-9 恢复；→ Kill-9 由 `chaos-worker-recovery.sh` 覆盖；竞争此前**无人验证**（实机只有一个 worker），现由 `scripts/lease-contention.sh` 补上：`worker-b` profile + 8 个任务 + 2 个 claimant，实测 `8 jobs, 2 workers, 160.0 credits settled once`。claimant 身份取自 `generation_attempts.lease_owner`（`generation_jobs.lease_owner` 在任务落定后会被清空，用它会把"没发生竞争"读成通过）。
- [x] Provider Contract Test；→ `contract-test` PASS（4 项，Provider 与 Payment 模拟器契约）。
- [x] 备份恢复后账本和资产哈希一致；→ 此前只做**相对增量**断言，整体漂移看不见，也没有任何一处把对象存储字节与资产哈希对拉过。现由 `scripts/restore_fidelity.py` 做绝对指纹（每租户各科目余额 + 每个资产导出包内 master 音频的 sha256），实测 restore 后 4 个资产字节一致、账本不动；反向对照已做：删掉某个已登记资产的 master 对象后判据立刻转红（`export 500`），再从备份恢复后转绿。
- [x] 跨租户测试为零泄露；→ 此前全仓只有 **1 条**跨租户断言（`GET /projects/{id}` 404）。现由 `services/acceptance/test_tenant_isolation.py` 扩到 10 条用例、覆盖约 20 个读端点与写端点，并配了「三租户」夹具（`db/migrations/010` 新增与买卖双方同角色的第三者租户，用于把 licenses/deliveries/payouts 的 seller-OR-buyer 策略钉住）。每条"看不见"的断言都与"属主确实看得见"配对，所以路由本身不存在、或夹具什么都没建出来，都不会被读成隔离成功。
- [ ] 至少 100 次真实 Provider 生成回归；→ 可数字化的一半已完成：`scripts/provider_regression.py` 实跑 100 个任务全链路（报价→冻结→Worker 领取→Provider 提交→媒体入库→结算），实测 `100/100 completed, error rate 0.0%`，逐任务校验 ready 数量、64 位 sha256、抽样真实下载、`settled_credits == 报价`，并核对账本只按结算总额移动、无悬挂冻结。**仍缺的是"真实 Provider"**：需要已签约的 Provider 与凭据，源码环境内无法产生；把 `MUSIC_PROVIDER` 指向真实适配器后同一套断言可直接复用。
- [ ] 正式 Provider 合同和能力证据。→ 商业/法务文件，非代码可得。

## G10 Commercial Launch

- [ ] 正式 Payment/KYC/Tax/Invoice；
- [x] 支付、退款、账本和 Provider 成本日终对账；→ **部分**：`scripts/reconcile_market.py` 已把订单/许可证/交付/分账/支出对拉成 13 条不变量（DB 为权威、API 为展示，两路读同一事实，任一侧漂都会红），Provider 实际成本与平台费的日终对账仍需真实账单。
- [ ] MFA、Secret Manager、WAF、限流和 SIEM；→ 已有应用层限流，真实值机与告警接入在目标环境。
- [ ] 内容审核、病毒扫描、投诉和 Legal Hold；
- [ ] 数据保护、隐私和用户删除/导出；→ 资产导出（`/assets/{id}/export`）与许可交付导出已实测可下载且字节稳定；面向用户的数据删除/可携带导出流程尚未实现。
- [ ] 灾难恢复演练；→ 参考 Compose 环境内的备份→恢复→绝对指纹校验已闭环（见 G9 第 4 项）；跨可用区/真实基础设施级恢复仍需在部署环境演练。
- [ ] 真实付费用户和正向贡献毛利。

## G11 Creator OS

- [ ] First Master Time 达标；→ 需真实用户漏斗数据。
- [ ] Master Conversion 达标；
- [ ] W4 Retention 和复购证据；
- [ ] 质量分与真人选择校准；→ 28 维打分引擎的**确定性**已实测（同输入两次得分一致），与真人偏好的一致性仍需人工标注样本。
- [ ] Studio 浏览器/移动端/无障碍测试；→ 无障碍此前只做了静态属性计数，未在真实浏览器点验。
- [ ] 团队协作和审批稳定。

## G12 Asset & Market OS

- [ ] Rights-ready Rate 达标；→ 经营指标。
- [x] License/Delivery/Refund/Payout 对账；→ `market-reconciliation` 步骤 PASS（13/13）。含"退款必须同时把订单/许可证/交付/支出四条一起翻"的链路断言，以及 8 条纯函数 85/15 策略的必红/必绿单测。
- [x] 商业许可试点和争议演练；→ **仅合成契约测试范围内**：授权→上架→购买→交付→退款撤销全链路已在真实栈上跑通。真实许可谈判与争议处理仍是线下事项。
- [x] 独家 Reservation 并发测试；→ 新增 `scripts/reservation_race.py` 驱动"甲预留→乙被拒→甲预留过期→乙购入并付款→甲再付旧单"，**实测到重复出售**（同一独家资产出现 2 张 active 许可证），已修复：`db/migrations/011` 的 `SECURITY DEFINER` 确认函数 + 来源态谓词。修复后同一驱动为 1 张许可证、落败方订单停在 `payment_pending` 且无许可证。
  - 顺带修正了一个更隐蔽的问题：原先 `market.py` 在买方事务里直接 `UPDATE offer_reservations/asset_offers`，而这两张表受租户 RLS 保护、买方看不到卖方行，所以那两条语句**一直匹配 0 行且不报错**——独家资产售出后从未真的被置为 `sold`。
- [ ] Brand Brief、Submission 和 Award 运营流程；→ 合成 award 全链路（含分成与撤销）已在 commercial 用例中跑通；运营侧流程待定义。
- [ ] 卖方 KYC/KYB 与税务；
- [ ] 市场流动性和佣金模型验证。

任何 Gate 的 Blocker 未解决时不得以"代码已完成"为由上线。

## 本轮附带发现（尚未处理，需属主决策）

1. `LOGIN_RATE_LIMIT_PER_MINUTE` 缺省 10 次/分钟且**按 IP** 计。真实部署里同一 NAT/出口 IP 后的正常用户会互相挤爆登录；本轮多个演练脚本正是被它挡住过（HTTP 429），当前做法是让脚本退避重试而没有放宽这个安全控制。
2. `record_license_revenue`（`db/migrations/002`）只写 `revenue_splits` 与 `payouts`，平台 15% 服务费**在积分账本里没有分录**。这不是 bug——积分账本按 credit 计量、服务费是货币——但 G10 的"日终对账"要真正闭环，需要一组货币计量的结算科目，而不是把分往 credit 余额里塞。
3. `reverse_license_revenue` / 退款回写里仍有同类"买方事务直接改卖方行"的语句（如 `market.py` 退款分支把独家 offer 置 `paused`），与上面第 2 条 G12-a 的成因同源，可能同样是静默 0 行；本轮只修了被实测到的售出确认路径，未一并改动退款路径。
