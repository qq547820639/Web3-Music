# 商业运营、账本与结算说明

## 货币单位

订单、支付、退款和 Payout 使用最小货币单位整数，例如 USD cents。Credits 是平台内部生成额度，不等同法定货币，不应作为可提现余额。

## Credit Pack / Subscription

```text
Order(pending)
→ Payment(processing)
→ signed webhook
→ Payment(succeeded)
→ Order(fulfilled)
→ Credits issued / Subscription active
```

Credits 发行通过 Ledger Transaction，不允许直接修改 Balance。

## 生成结算

```text
Quote
→ Credit Hold(original)
→ Provider/Media Result
→ settle Ready Candidates
→ release remainder
```

部分成功只结算成功候选；失败和取消释放未结算金额。

## License Purchase

```text
Active Offer
→ Reservation(exclusive only)
→ License Order
→ Payment
→ License(active)
→ Delivery(ready)
→ Revenue Split 8500/1500
→ Seller Payout(pending)
```

85/15 是参考默认值，应在正式商业模式中配置化并经过税务、合同和创作者协议确认。

## Brand Task Award

```text
Open Brand Brief
→ Rights-ready Submission
→ Owner Award Order
→ Payment
→ Brand License + Delivery
→ Submission/Brief awarded
→ Revenue Split + Seller Payout
```

Brand Award 与普通市场许可共享不可变 Rights Manifest、License、Delivery、Payout 和退款逆转规则，但订单主题固定为 `brand_submission`，预算来自 Brief。

## 退款

- Credit Pack：按累计退款比例计算应撤回 Credits；余额不足时拒绝自动退款并进入人工支持。
- Subscription：全额退款取消参考 Subscription。
- License：全额退款将 Order 设为 refunded，License 设为 refunded，Delivery revoked，Reservation released，独家 Offer paused，Payout reversed。
- 部分 License 退款当前只更新 Payment/Refund，不自动改变 License；生产业务必须明确部分退款政策。

## Payout

Payout 状态：`pending → processing → paid|failed`，失败可回到 pending，任何状态可在适当条件下 reversed。经济字段不可修改，记录不可删除。

本地系统只提供 Payout Queue，不连接银行。生产必须增加：收款人 KYC/KYB、税务表单、Reserve、负余额、付款批次、银行 Provider ID、失败重试、对账和人工双人审批。

## 日终对账

至少检查：

- Ledger Entry 总和；
- Hold 闭合；
- Job Settlement；
- Payment/Refund 总额；
- Order/License/Delivery 状态；
- Revenue Split = 10,000 bp；
- Payout 与退款逆转；
- Provider 实际成本与预估成本；
- 媒体与资产哈希。

差异不为零时阻止财务关账和 Payout。
