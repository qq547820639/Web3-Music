# 权利能力、证据与安全政策

## 原则

平台不使用单一 `commercial=true`。每个 Asset 的 Rights Manifest 对九项能力分别判断：

```text
stream, download, share, commercial_use, license,
sublicense, distribute, mint, content_id
```

状态：`allowed`、`blocked`、`manual_review`、`unknown`。

只有 `allowed` 可执行。其他状态均不得通过文案、按钮颜色或默认行为伪装为允许。

## Manifest 证据

Manifest 应绑定：

- Asset Snapshot 和媒体哈希；
- Provider、Adapter、模型、Capability Snapshot 和合同版本；
- 账户/套餐和生成时间；
- 用户对输入歌词、音频、声音和商标的声明；
- 人类贡献记录；
- AI 参与说明；
- 政策审核、Moderation Case、Legal Hold；
- Evidence ID 和 Reviewer；
- Manifest Version、Hash 和签发时间。

## 强制阻断

以下任一成立时，`commercial_use` 和 `license` 不得为 `allowed`：

- Provider 为 Emulator；
- Provider Approval 为 `development_only/unapproved/unknown`；
- 缺少必要输入权利声明；
- 在世艺人、普通人声音模仿或克隆缺少明确授权；
- Asset 存在 Legal Hold；
- 存在开放、限制或申诉中的内容案件；
- Rights Manifest 不是最新版本；
- Provider 条款或合同变化后尚未复核。

## 人工审核

人工审核不能绕过 Provider 合同硬阻断。审核只能在供应商基础权利允许的范围内补充证据和改变能力。

每次 Review 产生新 Rights Manifest，旧版本保留。审核操作记录 Reviewer、Evidence、理由和 Request ID。

## 商业 Offer

创建 Active Offer 必须：

- 资产属于卖方 Workspace；
- 最新 Manifest 的 `commercial_use` 与 `license` 都为 `allowed`；
- 没有 Legal Hold 或开放案件；
- License Template 有效；
- Offer 绑定当前最新 Manifest；
- 独家 Offer 没有有效 Reservation 或已售状态。

## License 和交付

License 必须冻结：卖方、买方、Asset、Rights Manifest、Template、Terms、Territory、期限、Licensee 和 Hash。退款、争议或权利限制可将 License 变为 suspended/revoked/refunded，并立即停止 Delivery。

交付包中的 Rights Manifest 和 License 是证据快照，不是版权登记或法律意见。

## 投诉和 Legal Hold

投诉受理后可立即：暂停 Offer、禁止导出、撤回公开分享、冻结 License、保留所有证据。Legal Hold 下不得删除 Asset、媒体、Rights、Audit、Payment 或 Ledger 记录。
