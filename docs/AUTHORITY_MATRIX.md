# 权限矩阵（由代码派生，不要手改）

`./scripts/authority_matrix.py --check` 会重算这张表并比对；派生自 `services/api/app/main.py` 与 `services/api/app/routers/*.py` 的路由装饰器与依赖签名，共 91 条 /api 路由，其中写操作 48 条。

读法：`require_roles` 一栏是端点自己声明的角色名单，名单之外的人在 `Depends` 阶段就拿 403；`工作区成员即可` 只检查调用者属于 `X-Workspace-Id` 那个工作区，`有效会话即可` 是主体自助通道（删除账户、注册第二因子）；`只有应用层鉴权` 的写路由一共 5 条，它们的保护不在这张表里而在端点内部——口令校验、刷新会话校验、按账号的限流、以及回调的 HMAC 签名——常驻用例逐条核对该端点源码里确实还写着那个载体，少一个就红（`tests/unit/test_authority_matrix.py`）。真正的授权担保还包括数据库层：RLS 与 `011`/`012`/`013`/`016`/`017` 的 `SECURITY DEFINER` 函数，端点检查只是门口那道 convenience。

`再认证` 一列是会话之外的第二道：5 条写路由在动数据库之前要求调用方当场再交出一次口令（已注册第二因子的账号还要一个当前验证码），判据是 `step_up_factors` 真的出现在端点函数体里，而不是请求体里有某个字段——它挡的是「Cookie 被拿走之后还能做什么」，所以设成没有确认窗口：一次凭证只够一次动作。


## 显式角色名单（require_roles）（59 条，写操作 33 条）

| 方法与路径 | 角色 | 再认证 | 端点 |
| --- | --- | --- | --- |
| `POST /api/admin/jobs/{job_id}/cancel` | admin,owner,support | — | `app:cancel_job` |
| `PUT /api/admin/v12/tickets/{ticket_id}` | admin,owner,support | — | `routers.admin_v12:update_ticket` |
| `POST /api/assets/{asset_id}/rights-evidence` | admin,creator,legal,owner | — | `routers.assets:add_evidence` |
| `POST /api/assets/{asset_id}/rights-review` | admin,legal,owner | — | `routers.assets:issue_reviewed_manifest` |
| `POST /api/brand-briefs` | admin,billing,creator,owner | — | `routers.market:create_brief` |
| `POST /api/brand-briefs/{brief_id}/submissions` | admin,creator,owner | — | `routers.market:submit_to_brief` |
| `PUT /api/brand-submissions/{submission_id}` | admin,owner,reviewer | — | `routers.market:update_submission` |
| `POST /api/brand-submissions/{submission_id}/award` | admin,billing,owner | — | `routers.market:award_submission` |
| `POST /api/events` | admin,billing,creator,legal,owner,reviewer,support,viewer | — | `routers.creation:capture_event` |
| `POST /api/jobs` | admin,creator,owner | — | `app:submit_job` |
| `POST /api/marketplace/offers/{offer_id}/purchase` | admin,billing,owner | — | `routers.market:purchase_offer` |
| `POST /api/moderation/cases` | admin,legal,owner,support | — | `routers.assets:create_case` |
| `PUT /api/moderation/cases/{case_id}` | admin,legal,owner | — | `routers.assets:update_case` |
| `POST /api/offers` | admin,creator,owner | — | `routers.market:create_offer` |
| `POST /api/orders/credits` | admin,billing,owner | — | `routers.market:create_credit_order` |
| `POST /api/orders/{order_id}/pay` | admin,billing,owner | — | `routers.market:pay_order` |
| `POST /api/orders/{order_id}/refunds` | admin,billing,owner | — | `routers.market:refund_order` |
| `PUT /api/preferences` | admin,billing,creator,legal,owner,reviewer,support,viewer | — | `routers.creation:update_preferences` |
| `POST /api/projects` | admin,creator,owner | — | `app:create_project` |
| `POST /api/projects/{project_id}/branches` | admin,creator,owner | — | `routers.creation:create_branch` |
| `PUT /api/projects/{project_id}/branches/{branch_id}` | admin,creator,owner | — | `routers.creation:advance_branch` |
| `POST /api/projects/{project_id}/chat` | admin,creator,owner | — | `app:chat` |
| `POST /api/projects/{project_id}/comments` | admin,creator,owner,reviewer | — | `routers.creation:create_comment` |
| `POST /api/projects/{project_id}/comments/{comment_id}/resolve` | admin,creator,owner,reviewer | — | `routers.creation:resolve_comment` |
| `PUT /api/projects/{project_id}/locks` | admin,creator,owner | — | `app:update_locks` |
| `POST /api/projects/{project_id}/master` | admin,creator,owner | — | `app:select_master` |
| `POST /api/projects/{project_id}/patch` | admin,creator,owner | — | `app:patch_project` |
| `POST /api/projects/{project_id}/quality` | admin,creator,owner,reviewer | — | `app:quality` |
| `POST /api/projects/{project_id}/quotes` | admin,creator,owner | — | `app:create_quote` |
| `POST /api/support/tickets` | admin,billing,creator,legal,owner,reviewer,support,viewer | — | `routers.market:create_ticket` |
| `POST /api/workspace/members` | admin,owner | — | `app:add_workspace_member` |
| `DELETE /api/workspace/members/{member_id}` | admin,owner | 是 | `app:remove_workspace_member` |
| `PATCH /api/workspace/members/{member_id}` | admin,owner | 是 | `app:change_workspace_member_role` |
| `GET /api/admin/dashboard` | admin,billing,legal,owner,support | — | `app:admin_dashboard` |
| `GET /api/admin/v12/dashboard` | admin,billing,legal,owner,support | — | `routers.admin_v12:dashboard` |
| `GET /api/admin/v12/moderation` | admin,legal,owner,support | — | `routers.admin_v12:moderation` |
| `GET /api/admin/v12/payments` | admin,billing,owner | — | `routers.admin_v12:payments` |
| `GET /api/admin/v12/reconciliation` | admin,billing,owner | — | `routers.admin_v12:reconciliation` |
| `GET /api/analytics/overview` | admin,billing,creator,owner,reviewer | — | `routers.creation:analytics_overview` |
| `GET /api/assets` | admin,billing,creator,legal,owner,reviewer,viewer | — | `routers.assets:list_assets` |
| `GET /api/assets/{asset_id}/provenance` | admin,billing,creator,legal,owner,reviewer,viewer | — | `routers.assets:asset_provenance` |
| `GET /api/assets/{asset_id}/rights-evidence` | admin,creator,legal,owner,reviewer,viewer | — | `routers.assets:list_evidence` |
| `GET /api/brand-briefs` | admin,billing,creator,owner,reviewer,viewer | — | `routers.market:own_briefs` |
| `GET /api/brand-briefs/public` | admin,creator,owner,reviewer,viewer | — | `routers.market:public_briefs` |
| `GET /api/brand-briefs/{brief_id}/submissions` | admin,owner,reviewer | — | `routers.market:review_submissions` |
| `GET /api/catalog` | admin,billing,creator,owner,reviewer,viewer | — | `routers.market:catalog` |
| `GET /api/deliveries` | admin,billing,creator,legal,owner,reviewer,viewer | — | `routers.market:list_deliveries` |
| `GET /api/deliveries/{delivery_id}/export` | admin,billing,creator,legal,owner,reviewer,viewer | — | `routers.market:export_delivery` |
| `GET /api/ledger` | admin,billing,creator,owner | — | `app:ledger` |
| `GET /api/licenses` | admin,billing,creator,legal,owner,reviewer,viewer | — | `routers.market:list_licenses` |
| `GET /api/marketplace/offers` | admin,billing,creator,owner,reviewer,viewer | — | `routers.market:marketplace_offers` |
| `GET /api/moderation/cases` | admin,legal,owner,support | — | `routers.assets:list_cases` |
| `GET /api/offers` | admin,billing,creator,owner,reviewer,viewer | — | `routers.market:own_offers` |
| `GET /api/orders` | admin,billing,creator,owner,reviewer,viewer | — | `routers.market:list_orders` |
| `GET /api/payouts` | admin,billing,owner | — | `routers.market:list_payouts` |
| `GET /api/preferences` | admin,billing,creator,legal,owner,reviewer,support,viewer | — | `routers.creation:get_preferences` |
| `GET /api/projects/{project_id}/branches` | admin,creator,owner,reviewer,viewer | — | `routers.creation:list_branches` |
| `GET /api/projects/{project_id}/comments` | admin,creator,owner,reviewer,viewer | — | `routers.creation:list_comments` |
| `GET /api/support/tickets` | admin,billing,creator,legal,owner,reviewer,support,viewer | — | `routers.market:list_tickets` |

## 平台管理员（8 条，写操作 3 条）

| 方法与路径 | 角色 | 再认证 | 端点 |
| --- | --- | --- | --- |
| `PUT /api/admin/switches/{key}` | — | — | `app:update_switch` |
| `PUT /api/admin/v12/payouts/{payout_id}` | — | — | `routers.admin_v12:update_payout` |
| `PUT /api/admin/v12/release-evidence/{gate}/{evidence_key}` | — | — | `routers.admin_v12:update_release_evidence` |
| `GET /api/admin/v12/payouts` | — | — | `routers.admin_v12:payout_queue` |
| `GET /api/admin/v12/release-evidence` | — | — | `routers.admin_v12:release_evidence` |
| `GET /api/admin/v12/release-gate/{gate}` | — | — | `routers.admin_v12:release_gate` |
| `GET /api/admin/v12/workspaces` | — | — | `routers.admin_v12:workspace_directory` |
| `GET /api/admin/v12/workspaces/{workspace_id}/members` | — | — | `routers.admin_v12:workspace_roster` |

## 工作区成员即可（get_actor）（11 条，写操作 2 条）

| 方法与路径 | 角色 | 再认证 | 端点 |
| --- | --- | --- | --- |
| `POST /api/candidates/{candidate_id}/media-token` | — | — | `app:candidate_media_token` |
| `POST /api/workspace/members/transfer` | — | 是 | `app:transfer_workspace_ownership` |
| `GET /api/assets/{asset_id}` | — | — | `app:asset_detail` |
| `GET /api/assets/{asset_id}/export` | — | — | `app:export_asset` |
| `GET /api/bootstrap` | — | — | `app:bootstrap` |
| `GET /api/jobs` | — | — | `app:list_jobs` |
| `GET /api/jobs/{job_id}` | — | — | `app:job_detail` |
| `GET /api/projects` | — | — | `app:list_projects` |
| `GET /api/projects/{project_id}` | — | — | `app:project_detail` |
| `GET /api/projects/{project_id}/revisions` | — | — | `app:revisions` |
| `GET /api/workspace/members` | — | — | `app:list_workspace_members` |

## 有效会话即可（get_user）（8 条，写操作 5 条）

| 方法与路径 | 角色 | 再认证 | 端点 |
| --- | --- | --- | --- |
| `POST /api/account/erasure` | — | 是 | `app:account_erasure` |
| `POST /api/auth/logout` | — | — | `app:logout` |
| `POST /api/auth/mfa/disable` | — | 是 | `app:mfa_disable` |
| `POST /api/auth/mfa/enroll` | — | — | `app:mfa_enroll` |
| `POST /api/auth/mfa/enroll/verify` | — | — | `app:mfa_enroll_verify` |
| `GET /api/account/export` | — | — | `app:account_export` |
| `GET /api/auth/me` | — | — | `app:me` |
| `GET /api/auth/mfa/status` | — | — | `app:mfa_status` |

## 只有应用层鉴权，端点内不再判定（5 条，写操作 5 条）

| 方法与路径 | 角色 | 再认证 | 端点 |
| --- | --- | --- | --- |
| `POST /api/auth/login` | — | — | `app:login` |
| `POST /api/auth/mfa/challenge` | — | — | `app:mfa_challenge` |
| `POST /api/auth/refresh` | — | — | `app:refresh` |
| `POST /api/payment-webhooks/{provider}` | — | — | `routers.market:payment_webhook` |
| `POST /api/provider-webhooks/{provider}` | — | — | `app:provider_webhook` |
