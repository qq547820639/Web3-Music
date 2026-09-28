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

上面两句是政策意图；下面两句是本轮实测到的实现面，二者不同，分开写才不会让政策冒充代码。

**受理入口已经有了**（此前这节写的「受理」在仓库里没有任何对外通道）。`db/migrations/021_public_rights_report.sql` 加 `rights_reports` 与五条 `SECURITY DEFINER` 函数，`POST /api/reports` 与 `POST /api/reports/status` 无需账号、无需会话即可使用，回执令牌是唯一的取回凭据；平台侧队列与人工挂案走 `GET /api/moderation/reports` 与 `POST /api/moderation/reports/{id}/link`，两者都要求平台管理员角色。三条设计约束各自有常驻判据（流水线第 15 步 `scripts/report_drill.py`，50/50）：真实 id、未知 id、畸形 id 三种投递返回同一个形状（收件口不能当目录探针）；举报人邮箱只存在于 `rights_reports`，不进租户能 `SELECT *` 读到的 `moderation_cases.evidence`；通知一经写入，含超级用户角色在内改不动也删不掉，处置只能另加一条记录。

**这五种处置动作里，平台目前真正拦得住的是三种。** `legal_hold` 生效的位置：市场上架（`db/migrations/002_creation_asset_market_os.sql:568,574`）、Offer 创建（同文件 `:675,680`）、品牌中标（`004_brand_award_commercial_flow.sql:25,30` 与更正其歧义列的 `009_prepare_brand_award_ambiguous_column.sql:26,31`），以及 Python 侧的 `assert_offer_rights`（`services/api/app/domain/commerce.py:48`，实调点 `services/api/app/routers/market.py:492,510,626`）——判据都是「最新 manifest 写了 legal_hold，或该资产上有 `legal_hold`／`open|triage|restricted|appealed` 的案件」。拦不住的两种，写清楚而不是假装拦得住：**禁止导出/下载**——`services/api/app/main.py:1230` 走的是 `services/api/app/domain/rights.py:43` 的 `allowed()`（这个行号在 2026-09-27 由 `scripts/hold_drill.py` 重取更正：原先写的 `:1183` 如今是`POST /api/moderation/reports/{id}/link`，指针在收件口那四条路由加进来时就漂了），它只看 `capabilities.download.status`，从不读 `legal_hold`；**冻结已发出的 License 与停止交付**、以及**付款/分账**与**主体自助删除**同样不看这条布尔。

**「保留所有证据」这一半也不是 Legal Hold 给的，而是全局 append-only 给的，而且它的名单比这句话窄。** 无差别拒绝 UPDATE 与 DELETE 的表：`audit_events`、`ledger_entries`、`ledger_transactions`、`asset_snapshots`、`rights_manifests`、`song_spec_revisions`、`contribution_events`、`provider_capability_snapshots`、`audio_candidates`、`master_selections`（`001:461-468`、`001:185-186`）与 `ai_runs`、`product_events`、`revenue_splits`（`002:460-464`）。名单之外是三处具体缺口：`rights_evidence` 与 `licenses` 只挂 `BEFORE DELETE`（`002:462-463`），**证据内容仍可改写、许可仍可改状态**（这一处在 2026-09-28 由 `db/migrations/022_frozen_evidence_and_license_columns.sql` 补上：按列冻结，`rights_evidence` 只放行 `status,reviewed_by,reviewed_at`，`licenses` 只放行 `status,activated_at`——正是上面"许可必须冻结"那句列出的那些维度不能动，而本节要求可变的 `suspended/revoked/refunded` 状态机照旧走得通；`scripts/hold_drill.py` 里八档读数两向都量过，产品自己那两次写 `services/api/app/routers/assets.py:217` 与 `market.py:372` 仍然落地）；`media_assets` 没有任何这类触发器，也就是**保留之下的媒体本身仍然删得掉**；`payouts` 的 UPDATE 走的是状态机守卫（`003:39`）而非冻结布尔。所以本节的字面承诺要按这两段读：政策写的是意图，上面列的是今天的机制，二者之间的差额登记在 `docs/RELEASE_CHECKLIST.md`「待属主定值」第 6 条——那条同时给出这条布尔挡什么、不挡什么的完整分母，以及「谁的保留算数」（保留由员工 `opened_by` 开立，与被删主体往往不是同一人）这个必须由法务/产品定的先后关系。

**而"媒体的行仍然删得掉"这句，主语今天只能是拿得到 SQL 的人。** 本轮实测：产品侧没有任何删除 `media_assets` 的路径（`grep -rn "DELETE FROM media_assets" services/ db/` 零命中，只有 `scripts/report_drill.py:407` 与 `scripts/hold_drill.py:494` 两处演练自己清探针时删），主体自助删除走的三条 `SECURITY DEFINER` 函数（`013`/`014`/`016`）删的是 `user_preferences` 与 `workspace_members`（外加会话与第二因子材料），从不碰资产或媒体行。所以给 `media_assets` 补一条 `BEFORE DELETE` 触发器，约束的是**运维写入**而不是用户流程；它的强度上限也低于名单上其他触发器——连库的 `music_admin` 是 superuser（`rolsuper=t`，实测），`SET session_replication_role=replica` 一次就把全名单的触发器一起绕开，本轮 `scripts/hold_drill.py` 清洗自己的探针用的正是这条。待属主定值第 6 条按这个口径读：先定"谁的保留算数"，再定这道以"约束运维"为名义的闸要不要补。
