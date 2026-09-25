# 发布门禁清单

状态口径（2026-09-25 实测更新）：

- `[x]` = 本轮在真实 Compose 环境执行并通过，权威运行是 `scripts/acceptance-all.sh` 在全新数据库上的 **15 步全 PASS + 1 步按开关跳过**（commit `1d8534e`，2026-09-25T18:39:55Z → 18:48:49Z，证据 `release-evidence/acceptance-20260925T183955Z/`；上一提交 `28deafc` 同套件亦全绿，说明可复现；浏览器验收另见 `release-evidence/browser-a11y-20260925T184749Z/report.json`，同一 commit）。第 16 行 `capacity-gate-500` 只有 `CAPACITY=1` 才执行，本轮记为 SKIPPED，其状态见 `FINAL_RELEASE_STATUS.md`（本机实测判红，属主机容量）。
- `[ ]` = 未通过或**无法在源码环境内完成**，每条都写明缺的是哪一份外部事实。
- 以往"本机无 Docker daemon，需在部署主机执行"的说法已失效：本轮 Docker 可用，跨容器验收已在真实 Compose 栈上跑通，本文按执行结果改写。
- 被验收的树以 `SUMMARY.txt` 里的 `git_commit=1d8534e` 为准；记录它的文档提交在其后，若文档之后再改动被测脚本，会重跑并改指新运行，而不是沿用旧读数。

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
- [x] 支付、退款、账本和 Provider 成本日终对账；→ **部分**：`scripts/reconcile_market.py` 已把订单/许可证/交付/分账/支出对拉成 15 条不变量（本轮 15/15，DB 为权威、API 为展示，两路读同一事实，任一侧漂都会红），且分账守恒另有数据库自己的约束兜底（见"附带发现·已澄清"第 1 条）。仍缺的是**外部账单**：Provider 实际成本与平台费的日终对账需要真实 Processor/Provider 结算单才能比对。
- [ ] MFA、Secret Manager、WAF、限流和 SIEM；→ 已有应用层限流，真实值机与告警接入在目标环境。
- [ ] 内容审核、病毒扫描、投诉和 Legal Hold；
- [x] 数据保护、隐私和用户删除/导出；→ **部分**（代码侧已闭环，法务侧待批）：资产导出（`/assets/{id}/export`）与许可交付导出实测可下载且字节稳定；面向主体的两条权利通道本轮补齐并常驻验证——`GET /api/account/export`（账户、成员关系、会话、偏好、事件，以及按租户逐个读取的 19 组 `table.column` 记录）与 `POST /api/account/erasure`（`db/migrations/013`+`014` 的 `SECURITY DEFINER` 函数）。`scripts/erasure_drill.py` 32 项断言全过（证据见 `FINAL_RELEASE_STATUS.md`）。删除采取"身份匿名化 + 切断访问 + 保留假名化财务与溯源"而非硬删除，这是数据库结构决定的：`song_projects` 等对 `users(id)` 是 NOT NULL 外键，且 `audit_events`/`song_spec_revisions` 带 append-only 触发器（演练里真的试着删一次，要求数据库拒绝，结果实录 `ERROR: song_spec_revisions is immutable`）。**仍缺外部事实**：把"匿名化即删除"作为合规处置写进 DPA/保留政策并由法务签署，以及主体请求的线下受理流程（本仓库无注册端点，账户由运维开通）。另记一处实测事实：`product_events` 的唯一写入点是 `POST /api/events`（`services/api/app/routers/creation.py:211`），而此前没有任何用例调用它，所以该表在本机栈上 0 行、导出里的 events 一条也没被走到过；演练现在先投一条 `candidate_played` 再断言它出现在导出里，这条读路径才算被测过。
- [ ] 灾难恢复演练；→ 参考 Compose 环境内的备份→恢复→绝对指纹校验已闭环（见 G9 第 4 项）；跨可用区/真实基础设施级恢复仍需在部署环境演练。
- [ ] 真实付费用户和正向贡献毛利。

## G11 Creator OS

- [ ] First Master Time 达标；→ 需真实用户漏斗数据。
- [ ] Master Conversion 达标；
- [ ] W4 Retention 和复购证据；
- [ ] 质量分与真人选择校准；→ 28 维打分引擎的**确定性**已实测（同输入两次得分一致），与真人偏好的一致性仍需人工标注样本。
- [x] Studio 浏览器/移动端/无障碍测试；→ 此前只做过 `aria-`/`role=` 的**源码计数**（`ITERATION_CHANGES_PHASE3.md` 明确记为"无浏览器，仅静态硬化"），真实浏览器一点就发现静态计数看不见的东西。现由 `scripts/browser_a11y.py` 用 Playwright + axe-core 4.13.0（sha256 固定）在真实栈上跑：登录→建项目→跑 28 维质量→出报价→开版本历史对话框→四个主视图＋市场四个页签→管理后台五个视图，桌面 1440 与手机 390 两档，共 36 次 axe 扫描、62 个视图记录，结果 **critical 0 / serious 0**，moderate 48 项集中在 `heading-order`、`landmark-one-main`、`region` 三条（未达本 Gate 判据，作为后续项列出，不算已修）。同轮还量到并修掉：管理后台工作区 `<select>` 无可访问名（axe 判 critical）、顶栏 grid 项在 390px 下把页面撑到 435px、以及下面"附带发现"第 3 条那类被自家 CSP 静默作废的内联样式。判据本身带牙：`--self-test` 会植入一张无 alt 的图（axe 报 critical）、一面强制 `script-src 'self'` 的 meta（浏览器必须拒绝内联脚本）、一段 3 秒色彩过渡（采样器必须拒绝判为已稳定），另有 12 条单测钉住判决函数的必红/必绿两侧。
- [ ] 团队协作和审批稳定。

## G12 Asset & Market OS

- [ ] Rights-ready Rate 达标；→ 经营指标。
- [x] License/Delivery/Refund/Payout 对账；→ `market-reconciliation` 步骤 PASS（15/15）。含"退款必须同时把订单/许可证/交付/支出四条一起翻"的链路断言、独家上架/售出/退款撤架的状态断言，以及 8 条纯函数 85/15 策略的必红/必绿单测。
- [x] 商业许可试点和争议演练；→ **仅合成契约测试范围内**：授权→上架→购买→交付→退款撤销全链路已在真实栈上跑通。真实许可谈判与争议处理仍是线下事项。
- [x] 独家 Reservation 并发测试；→ 新增 `scripts/reservation_race.py` 驱动"甲预留→乙被拒→甲预留过期→乙购入并付款→甲再付旧单"，**实测到重复出售**（同一独家资产出现 2 张 active 许可证），已修复：`db/migrations/011` 的 `SECURITY DEFINER` 确认函数 + 来源态谓词。修复后同一驱动为 1 张许可证、落败方订单停在 `payment_pending` 且无许可证。
  - 顺带修正了一个更隐蔽的问题：原先 `market.py` 在买方事务里直接 `UPDATE offer_reservations/asset_offers`，而这两张表受租户 RLS 保护、买方看不到卖方行，所以那两条语句**一直匹配 0 行且不报错**——独家资产售出后从未真的被置为 `sold`。
- [ ] Brand Brief、Submission 和 Award 运营流程；→ 合成 award 全链路（含分成与撤销）已在 commercial 用例中跑通；运营侧流程待定义。
- [ ] 卖方 KYC/KYB 与税务；
- [ ] 市场流动性和佣金模型验证。

任何 Gate 的 Blocker 未解决时不得以"代码已完成"为由上线。

## 本轮附带发现

### 已修（含复现证据）

1. **自家 CSP 把自家 UI 静默作废。** `services/web/nginx.conf` 发的是 `style-src 'self'`，而 `app.js` 有 6 处 `el.style.x=` 和 4 处模板内联 `style="…"`——浏览器会**整条拒绝**内联样式，于是 28 维雷达的分组色点 `getComputedStyle` 实测为 `rgba(0,0,0,0)`、数据多边形丢填充、hover 提示不移动、试听进度条不动，而源码里"确实设了背景色"。真实浏览器渲染雷达时喷出 10 条 CSP 违规，且**没有任何测试工具在页面里**（先跑一遍无 axe 的同流程才把"应用的"和"扫描器的"两类留痕分开）。改法是不用内联样式：分组色走 tone class、提示走 SVG `transform` 属性、进度走原生 `<progress value>`。验收脚本保留一道常驻断言：按出厂 CSP 加载，被拦下的内联样式数必须为 0。
2. **UI 代理把 API 的死地址钉住了。** `proxy_pass http://api:8000` 里的字面主机名只在配置加载时解析一次，api 容器一旦被重建（本轮商业覆层 `up` 就重建了），nginx 仍连旧 IP：日志实录 `connect() failed (111) while connecting to upstream http://172.19.0.8:8000`，而 api 当时在 `172.19.0.7`，Studio/后台每个 `/api/` 调用都 502，API 本身却是健康的。改用 Docker 内嵌 resolver + 变量后强制换址复验（.7 → .13，web 容器全程不重启）：连点三次登录均 200。
3. **错误路径把真因吃掉了。** 两个前端都先 `response.json()` 再在 `catch` 里 `response.text()`——流已被读干，第二次抛 `body stream already read`，用户看到的就是这句 TypeError。改成读一次再按需解析后，同一界面立刻报出真因 `"too many login attempts"`，上面第 2 条与下面"待决"第 1 条都是靠这个才定位到的。
4. **退款路径的静默 0 行**（上一版本文列为"未一并改动"）：`market.py` 退款分支在买方事务里直接把独家 offer 置 `paused`，与 G12-a 的成因同源；实测复现 14/15，`db/migrations/012` 换成 `SECURITY DEFINER` 的 `pause_marketplace_offer_on_refund` 后 15/15，退款后 offer 状态实测为 `paused`。
5. **交付清单撒谎。** `SOURCE_MANIFEST.sha256` 是被引用为发布证据的入库文件，却没人校验：按修好后的判据对 `e1a8957`（本轮改动前最后一个提交）复算，188 个跟踪源文件里清单只覆盖 154 行（migration 007–012 全在缺席的 34 个里），且这 154 行有 44 行哈希已对不上内容——**只有 110/188 被如实描述**，而文件本身看起来像一道完整性校验。现在 `static-verify.sh` 会调 `scripts/source-manifest.sh check`，源码改了却没刷新清单 CI 就红；`write` 仍是独立命令，否则这道闸永远不会红。五侧对照已在临时仓库验过：漏一条、翻一个十六进制字符、枚举到 0 个文件都判红，刚写好的清单与历史 `./path` 写法判绿。这道闸上线后第一件事就抓到了自己过滤器的 off-by-one（`release-evidence/` 漏排除，表现为提交验收件后 `stale=6`），上面的数字是该修复之后的复算，首读（194/40/112）已作废——它现在还留在 `28deafc` 的提交说明里。
6. **导出把 `brand_submissions` 的租户列当作 `workspace_id`，整份导出 500。** 面向主体的记录名单是从 migration 里的 `REFERENCES users(id)` 推出来的，但查询句假定每张表都带 `workspace_id`；`brand_submissions` 的租户策略写的是 `submitting_workspace_id`（`db/migrations/002_creation_asset_market_os.sql:496`），于是 `SELECT * FROM brand_submissions WHERE workspace_id=…` 直接抛 `column "workspace_id" does not exist`，导出整体 500。改法是把名单从二元组升为三元组（表、人列、租户列）。这条的常驻牙齿是"导出必须 200"本身：循环对每张表都发一次查询，租户列再写错就是 500 而不是静默漏一张表。同一轮还加了覆盖自检——导出正文里声明 `coverage`，演练把 `information_schema` 里所有指向 `users(id)` 的外键列拿来作差；正向对照做过：临时建一张带 `user_id REFERENCES users(id)` 的表，判据精确点名 `tmp_probe_link.user_id` 并判红，删表后转绿。另有 4 条不依赖数据库的 AST 单测钉住名单元数、可注入面（这些名字要进 f-string SQL）与重复键。
7. **删除函数撞上自家会话不可变触发器，每一次删除都以 409 失败。** 013 在撤销会话时顺手把 `expires_at` 提前，而 005 的 `guard_auth_session_update`（`db/migrations/005_final_release.sql:44-54`）把 `expires_at` 列入不可变字段：实测每次调用都抛 `immutable auth session fields cannot change`，事务回滚、账户原样不动。`db/migrations/014` 去掉那一列写入即可，且这不是绕路：认证路径本来就按 `revoked_at` 拒绝（`services/api/app/auth.py:123/150/178`），应用自己的两处撤销点（`services/api/app/main.py:177` 轮换、`:188` 登出）也只写 `revoked_at/revoke_reason`。演练把这条钉成一句断言：撤销确实落地，且 `expires_at` 一个字节都没动。
8. **RLS 让导出的空读伪装成成功——与 011/012 同族，只是方向从写变成读。** `user_preferences` 与 `product_events` 都开了 `FORCE ROW LEVEL SECURITY`（实测 `relrowsecurity=t, relforcerowsecurity=t`），不带租户作用域去读就是 0 行且不报错：导出返回 200、`preferences` 键是 `[]`，而那一行明明在表里——是同一次删除报出的 `preferences_removed=1` 与它对照才暴露的。改成按成员关系逐租户设作用域再合并。同轮补上事件侧的空白：`product_events` 唯一的写入点 `POST /api/events`（`services/api/app/routers/creation.py:211`）此前没有任何用例调用，所以那条读路径从未被走到，演练现在先投一条 `candidate_played` 再断言它出现在导出里。

### 已澄清（上一版记为"需属主决策"，读码 + 实测后确认不是缺口）

1. **平台 15% 服务费不是"没有分录"。** 上一版本文说它在账本里没有分录，一半说对了、一半说错了：`ledger_accounts` 里 15 个账户的 `currency` **全部是 `CREDIT`**——积分账本按 credit 计量，货币收入本就不该往这里塞；货币侧分录在 `revenue_splits`，`record_license_revenue` 为每张许可证写一对 `(workspace 8500, platform 1500)`，实测库里 6 张许可证正好 6+6 行、合计 60000bp。更硬的一层是数据库自己：`revenue_split_total_must_balance` 约束触发器要求同一 subject 的分账和恰为 10000bp，两向探针实测——成对插入被接受（`INSERT 0 2`，sum=10000），只插 9000 被拒 `revenue split total must equal 10000 basis points, got 9000`，探针全部回滚、残留 0 行。所以 G10 这条剩下的只是"拿真实结算单比对"，不是"补一套货币科目"。

### 待属主定值（不自行放宽安全控制）

1. **登录限流是按账号，且"拒绝也算一次"。** 先前本文写成"按 IP"是错的（那句 429 的症状被误读成按 IP）。读码 + 实测：键是 `login:sha256(email)`（`services/api/app/main.py:157`），第 11 次尝试起 429；随后每 30 秒试一次，连着 90 秒都进不去，直到**完全静默 75 秒**才恢复——因为 Lua 对每次 INCR 都无条件 `EXPIRE 60`，被拒的请求也会续期，窗口会随敲门的频率滑动。后果两面：同一出口 IP 的正常用户互不影响（原担忧不成立）；但任何人只要知道某个邮箱，就能持续把该账号挡在登录门外（无验证码、无 IP 维度），而对多账号分散撞库没有任何总闸。修法只需一行（只在 `n == 1` 时 `EXPIRE`，变成真正的固定窗口），但那是在改一条认证侧的安全控制，留给属主定夺；本轮只把**测试客户端**的退避改成实测可恢复的 75 秒以上（含 `browser_a11y.py` 与 `e2e_client.py`），没有动服务端缺省值。
2. **导航在 boot 完成前点了没反应。** `#app` 去掉 `hidden` 早于 `bindNavigation()`（它在首屏数据加载完之后才绑），所以首屏刚出来点顶栏按钮会被静默丢弃。验收脚本用"重试直到视图真的切换"绕过，但用户在慢网络下点到的就是没反应的按钮——要么把事件绑定提前到 DOMContentLoaded，要么在绑定完成前给出禁用态。
3. **两个前端共用一条会话 Cookie。** `localhost:4173` 与 `:4174` 同主机不同端口，Cookie 不分端口，所以 Studio 点"退出"会连带把管理后台会话一起作废（脚本里表现为后台首屏 401 留痕）。生产若把控制面放到同主机的另一端口，这就是一个真实的联动；需要域或路径隔离才算定案。
4. **匿名首屏把预期内的 401 记成 console error。** `init()` 里 `/api/auth/me` 未登录时返回 401 属正常流程，却被 `console.error` 记录（本轮 16 条 console 留痕里除了 CSP 全是它）。不影响功能，但会污染浏览器端的错误监控。
5. **axe 还有 48 项 moderate**（`heading-order` / `landmark-one-main` / `region`），未达本 Gate 的判据线（critical/serious），但属于 WCAG 可达性问题，排后续轮次。
