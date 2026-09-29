# v13 Final 最终产品验证报告

生成日期：2026-08-07

## 本交付环境实际执行

| 验证 | 结果 |
|---|---|
| Python `compileall`（services/tests/scripts） | 通过 |
| Creator Studio 与 Admin JavaScript `node --check` | 通过 |
| Shell 脚本 `sh -n` | 通过 |
| 基础与商业 Compose YAML 解析 | 通过 |
| 所有 JSON 解析 | 通过 |
| JSON Schema 元模式校验 | 通过 |
| OpenAPI JSON/YAML 一致性 | 通过 |
| 架构与禁用旧模式审计 | 通过 |
| 单元测试 | 17/17 通过 |
| 28 维质量引擎确定性与 v2.1 数学关系 | 通过 |
| 普通话/粤语歌词量表 | 通过 |
| 父子 JSON Pointer 锁定 | 通过 |
| Media Token 绑定与过期 | 通过 |
| Emulator Rights 商业阻断 | 通过 |
| Payment Intent/Refund 幂等与超额退款阻断 | 通过 |
| Provider Emulator 幂等、冲突、部分成功与 WAV | 通过 |
| API `/health` 与 Prometheus Middleware | 通过 |
| OpenAPI | v13.0.0，69 条路径 |
| Compose 服务定义 | 12 个服务 |

实际静态命令：

```bash
./scripts/static-verify.sh
```

实际结果：

```text
Compose, JSON, JSON Schema and OpenAPI contracts valid
architecture contracts valid
17 passed
```

## 随包提供、但未在本构建容器执行的 Docker 验收

本构建环境没有 Docker/Podman 守护进程和 PostgreSQL 服务，因此没有虚构以下跨容器结果：

- PostgreSQL Migration 与真实 RLS；
- Postgres Lease Worker、Heartbeat 和 Kill-9 恢复；
- MinIO 私有媒体入库；
- Redis Outbox；
- Payment Webhook 跨容器回调；
- 备份恢复后的完整重验；
- 合成商业许可端到端链路。

这些测试已经编码到 Compose Acceptance 和 GitHub Actions。安装 Docker 的主机运行：

```bash
cp .env.example .env
docker compose up --build -d
docker compose --profile test run --rm acceptance
./scripts/contract-test.sh
./scripts/chaos-worker-recovery.sh
```

商业闭环：

```bash
docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml \
  --profile commercial-test run --rm acceptance-commercial
```

## 默认 Acceptance 覆盖

- JWT、Membership、RBAC、平台管理员边界和跨租户 404；
- Web/Admin 页面和 API/Worker Metrics；
- Revision、锁定、AI/手动 Patch、质量重放；
- Quote 固定 Revision/Provider Snapshot/费用；
- Job 幂等、冲突、成功、部分成功、失败、超时与取消；
- 独立 Hold、结算、释放与账本余额；
- 私有媒体 Token、音频下载和 SHA-256；
- Master、Asset Snapshot、Rights Manifest 与个人资产导出；
- Webhook 签名与重复事件去重；
- 分支、评论、偏好、产品事件和支持工单；
- Credit Order、Payment、Refund、Credits 回收；
- 默认 Provider 无法建立商业 Offer。

## Commercial Acceptance 覆盖

- 显式 `synthetic_licensed + approved_commercial` 测试快照；
- 权利审核后创建独家 Offer；
- 跨 Workspace 购买与 Reservation 隐藏；
- Payment 成功后激活 License 和 Delivery；
- 下载包含音频、License、Rights Manifest 与 Asset Snapshot 的 ZIP；
- 85/15 Revenue Split 和 Seller Payout；
- 全额退款撤销 Delivery、改变 License、释放 Reservation、暂停独家 Offer并逆转 Payout。

## 真实商业上线仍需外部证据

- 正式音乐 Provider 合同与至少 100 次真实 Contract/Cost/Rights 回归；
- 正式支付机构、税务、发票和退款对账；
- 500 并发生成、数据库主从切换、对象存储故障和 DNS Rebinding 专项；
- 独立安全测试、内容审核和版权投诉演练；
- 真实用户满意 Master、留存、退款率和贡献毛利数据。

## 500 并发低成本强化增量验证

本增量交付在当前无 Docker daemon 的交付环境中实际执行：

- Python compile / YAML / JSON / Schema / OpenAPI 静态验证：通过；
- Kubernetes YAML 解析：通过；
- 架构审计：通过；
- 单元测试：28/28 通过；
- 新增 PostgreSQL 连接池、Worker 并行、Provider HTTP 连接复用、presigned 媒体直出、容量索引和容量 Gate 脚本：已完成代码与静态验证。

由于本交付环境没有 Docker daemon，**没有宣称 500 并发已经实测通过**。在部署/预发布 Docker 主机执行 `./scripts/capacity-gate-500.sh` 后，其日志才是 500 并发 Gate 的有效运行证据。

## 2026-09-25 追加：真实执行结果

上文各表是当次打包的静态/单元记录，保留原文不再改写。本轮 Docker 可用，验收已在真实 Compose 栈执行：

- 权威运行（2026-09-29）：`scripts/acceptance-all.sh`，全新数据库（本轮起清库归流水线：`FRESH=1` 时脚本自己在起栈前 `down -v`，并在 `SUMMARY.txt` 头部同一行记下 `fresh_database=1`，上一轮那份 SUMMARY 里没有这一格），**22 步 PASS + 1 步按开关跳过（共 23 行）**，commit `680a0ea`，2026-09-29T02:26:10Z → 2026-09-29T02:49:48Z，逐步日志与 compose 日志见 `release-evidence/acceptance-20260929T022610Z/`，浏览器结果为 `release-evidence/browser-a11y-20260929T024252Z/report.json`，启动时宿主 `host_load="5.75 5.52 5.54"`、Docker VM 4 vCPU（这两个读数由同一份 SUMMARY 自己记录）。规定动作（上一轮立下：记录里的 `git_commit` 必须就是被测的那棵树，而不是「HEAD 加暂存区」）本轮照做。上一轮权威运行（`fc70d13`，20 行、19 PASS + 1 跳过，`release-evidence/acceptance-20260926T232902Z/`）、再上一轮（`99d5847`，同为 20 行，`release-evidence/acceptance-20260926T083402Z/`）与 `93b4984`（`release-evidence/acceptance-20260926T075659Z/`）、更早的 `2c3ef7f`（18 行，`release-evidence/acceptance-20260925T234750Z/`）作为历史读数保留。可复现性：此前同一主机已有 36 次 `FAIL=0`（`82f2ffe`, `5028686`, `28deafc`, `1d8534e`, `01e61d1`, `2c3ef7f`, `96d5955`, `b79e70b`, `3da3920`, `93b4984`, `99d5847`, `1f19952`, `414752d`, `aa3605b`, `fc70d13`, `cb8b901`, `cc63bee`, `2f25fe5`, `037b818`, `ef88d14`, `3dbd175`, `1dbf99e`, `fb6ff39`, `a841628`, `d98e650`, `c9e3cbd`, `c007b21`, `c469bf3`, `e75dcc6`, `e699e60`, `01147cf`, `8f69a1e`, `fa64cef`, `09be2ab`, `16e2660`, `83f19ad`，旧→新，当时分别 15/15/16/16/17/18/19/20/20/20/20/20/20/20/20/21/21/21/22/23/23/23/23/23/23/23/23/23/23/23/23/23/23/23/23/23 行——套件每轮都在变宽，共同命题是「每次跑完当时全部行」而不是「同样 20 行绿了 8 次」）。发现记录同样保留（留档判红共 30 份（26 年 09 月 25 日 7 份、26 年 09 月 26 日 8 份、26 年 09 月 27 日 5 份、26 年 09 月 28 日 8 份、26 年 09 月 29 日 2 份），每一条都是改坏东西的真实捕获）：`acceptance-20260926T021711Z` 停在第 3 步——验收套件用自己那份 `PROVIDER_WEBHOOK_SECRET` 字面量签名，而 acceptance 容器从没被给过这个变量（见下条第 11 项）；`acceptance-20260926T022643Z` 停在第 17 步 98/100——两个候选的原因被记成读不出的 `{'type': 'ReadError', 'message': ''}`（见下条第 12 项）；`acceptance-20260926T093142Z` 停在第 19 步 1 条——该轮新加的验证码输入框带着 hidden 却仍然渲染，顺着它量出登录页长期叠在每一个已登录视图之上（见下条第 16 项）。与 `acceptance-20260926T112458Z`（第 10 步：provider emulator 在 `async def` 处理器里同步合成候选音频，一次幂等重放把整个进程按住 9.98s，同时把一次无关的 `/health` 拖到 9.95s）与 `acceptance-20260926T115637Z`（第 19 步：本机另一个项目的 `vite preview` 抢下 4173 的 IPv6，`localhost` 优先走 ::1，门禁审计的是别人的站点）。`acceptance-20260926T154804Z`（第 2 步 compose-up：宿主取 PyPI 掉到 23 kB/s，pip 回 `(from versions: none)`，而一行镜像构建没有任何重试，当时那 20 行的链第 2 步就停住，其余 18 行一步没跑到 → 附带发现第 19 条） 与 `acceptance-20260926T161826Z`（同一步：重试加上之后红的是**并发**——`up --build` 把 worker 的 `apt-get install ffmpeg` 与另两个镜像的 pip 下载挤在同一时刻，apt 被 SIGKILL（退码 137）→ 同条）。本轮两支：`acceptance-20260927T030055Z` 停在第 6 步 lease-contention——链路的构建循环当时只枚举 `docker compose config --services`，挂在 profile 后面的 `worker-b` 因此从未被重建，那台几个小时前构建的旧镜像把字面量 `clean` 写进 `media_assets.scan_status`，被迁移 020 的触发器拒收、一个作业就此落成 `partial`，报出来的却是一句关于 lease 的话（修复 `d9687d8`：循环改从 `docker compose --profile '*' config --format json` 取服务，并在提交作业之前比对宿主 `services/worker/worker.py` 与两个 claimant 容器内 `/app/worker.py` 的哈希）；`acceptance-20260927T035001Z` 停在第 5 步 chaos-worker-recovery，报 "expired lease was not reclaimed by the restarted worker"——库里那条 lease 其实已经被回收，回收者是 18 分钟前手工起的 `worker-b`（`generation_jobs.lease_owner` = `worker-0a906393`，仍在心跳），不是这一步杀掉、自称 `worker-e02a9f18` 的那个 worker（修复 `cb8b901`：该步进入时移除 `worker-b`，并要求 compose 只报告一个名字以 `worker` 开头的运行中服务，失败消息带上它最后读到的 status 与 attempt 计数）。更早的两条（`acceptance-20260925T223656Z` 第 1 步红 = 清单写早了；`acceptance-20260925T223811Z` 第 15 步 87/100 红 = 主机 I/O 停摆并暴露「终态失败不带原因」）不动。
- 单元测试：28 → 54 → 66 → 70 → 78 → 114 → 176 → 183 → 189 → 196 → 210 → 343 → 382 → 409 → 423 → 434 → 437 → **481**（末位是权威运行 `acceptance-20260929T022610Z` 第 1 步的读数，由盖章格取自 SUMMARY 的 `metrics static-verify unit_passed=` 行；437 及其之前每一格是写下它那一轮的读数，382 与 409→423 的增量见 `docs/RELEASE_CHECKLIST.md` 第 30 条，423→434、434→437 见 `docs/FINAL_RELEASE_STATUS.md` 的 2026-09-28 两节。382 那一轮 +39：021 公开面的静态守卫 28 条（14 判据 + 14 必开火对照，共 33 次内存注入，其中一支是词表被写成三份这件事本身——Python 常量、021 的两条 `CHECK (col IN ...)`、以及 `file_rights_report` 函数体内 `= ANY (ARRAY[...])` 的第三份）+ 数据库拒绝到 HTTP 状态的映射 11 条（四档真源码变异各只红自己那一条，第五档证明还原逐字节相同）；上轮 +14：再认证面 13 条（矩阵的「再认证」列必须恰好是那 5 条写路由、reader 对合成端点的两支反证——调了要认、只当依赖用不认、GET 一律不该带、五条路由都从请求体取凭据、五个模型都还混着 `StepUp`、401 与 403 的分配、把「口令错」答成 401 的变异必须开火、只计失败且答对要清零、拒绝必须记在账号名下而不是 system、三个名册处理器都把凭据发出去、口令框的 `type`/`autocomplete`、验证码框只随武装状态出现）+ 浏览器门禁 1 条（hidden 扫描，含「分母为零要开火」与「`require=False` 时不得开火」两侧）；上一轮 +7：权限矩阵 7 条——派生结果必须等于 FastAPI 自己的路由表、两份成对工件不得与代码漂移、五条「门口不查角色」的写路由逐条点名其保护载体并核对那行代码确实还在、`require_roles` 用到的名字必须落在 `001` 的词表里、两支 fixture 反证 reader 曾漏掉的形状；上一轮 +6：名册面板 3 条（面板调用的路径必须来自 FastAPI 自己的路由表、面板选的 id 必须在文档里存在、角色名单必须是从 `pg_constraint` 读出来的而不是 Python 里再抄一遍）+ 危险按钮对比度 3 条（两个应用的 `button.danger` 前景/背景都按 WCAG 公式算，不再等 axe 现场判）；上一轮 +7：隐私面板的静态守卫 7 条，八个变异全部证明会开火（改下载路径、放松确认词守卫、在渲染器里塞 `innerHTML`、改元素 id、摘掉 `role="alert"`、把删除端点加进 CSRF 豁免名单、还原 401 守卫、抽掉 `admin.js` 的 `status` 接线）；上一轮 +62：第二因子原语 22 条含 18 条 RFC 6238 公开向量与一份独立 stdlib 实现并排、成员写入静态守卫 7 条（每条配必开对照）、签名密钥策略 16 条、环境契约 9 条（三种语法、两向求差、白名单每项都要仍为真）、媒体入库重试与错误签名 7 条、导出口径 +1；`pytest -q tests/unit` 无需起栈，2-3 秒）。
- 跨容器常驻用例：默认栈 11 项（备份恢复后复跑再次通过）；商业覆层 11 项（1 项商业全链路 + 10 项跨租户隔离）。
- 第二因子演练：`scripts/mfa_drill.py` **79/79**（流水线第 12 步；上一版 56，新增的两条不再靠看图：`qr_png_data_uri` 必须被一个独立解码器读回、且逐字等于同一响应的 `provisioning_uri`）。码由 `e2e_client.totp_code()` 现场生成——那是从 RFC 现写的一份 stdlib 实现，与被测服务端用的 pyotp 不同源，否则「绿」只说明两边犯了同一个错。断言里最有分量的四条：待用凭据不能当访问令牌用（反向也一样）、已用过的码换一个待用凭据再放进来仍被拒（重放闸按账号存在库里而不是按进程）、注册时另一条已开会话不会跟着被放行、换种子后旧恢复码在存储的 HMAC 集合里已经不存在。本轮再加一条顺序性质：关闭两步验证要先交口令，口令判错时**验证码不被消耗**——演练用同一个码走两次，第一次被口令挡下、第二次配正确口令才真的关掉，所以「拒绝」不会顺手烧掉用户的一次因子。`Codes` 那份按 30 秒窗口取码的夹具也从 mfa_drill 单点搬进了 `scripts/e2e_client.py`，因为 erasure 演练现在同样要花码。
- 成员与角色演练：`scripts/member_drill.py` **98/98**（流水线第 13 步；33 → 39 → 47）。新增的 6 条只问一件事：角色名单到底从哪来——端点返回的名单必须逐字等于 `workspace_members_role_check` 的数组、也必须等于 017 里 `workspace_role_error` 拼写出的那组，并且名单里的每个名字都要真能被写路径接受（逐名发一次 add，用「无此账户」这个失败原因证明角色那关已过）。其中两条以 `music_app` 身份真发 `INSERT` / 自升 `UPDATE` 并要求数据库回答 permission denied，一条以伪造 actor 直接调 017 的函数，一条把「唯一属主 → 删除被拒 → 移交 → 删除成功」这条曾经走不通的链跑完。本轮再加的 8 条全是「会话之外那一道」：改角色不给口令必须 401 且名册一字不动、给错口令必须 403 且名册一字不动、口令这道墙排在领域规则之前（所以那句「先去移交所有权」不会在没验身份时先漏出来）、移交所有权同样要口令、以及末尾一条按时钟生效的 429——同一账号连错六次口令之后，第七次**连正确的口令**也会被挡在外面，且这一整段跑完之后名册仍未被写过。
- 媒体扫描边界演练：`scripts/media_scan_drill.py` **14/14**（流水线第 14 步）。这一步只问一句：`clean` 这个状态是谁给的——伪造判决（没有引擎出处的 `clean`、有出处的 `unscanned`）必须被 020 的触发器拒，诚实记为 `unscanned` 的资产必须照常可听，探针行全部包在 `BEGIN…ROLLBACK` 里并清点无残留。
- 对外侵权通知收件演练：`scripts/report_drill.py` **50/50**（流水线新第 15 步）。真实 id／随机 uuid／畸形字符串三种投递的响应逐字段相同（这道门不能回答「这个东在本平台吗」）；举报邮箱不出现在租户 `SELECT *` 读得到的案件里；通知建成后以超级用户角色也改不动删不掉；两条窗口（按地址、按全站）各自有界且拒绝一次不花费；收尾三条「teardown 没留下夹具工作区／通知／案件」的自证。
- 部署侧签名密钥：`docker-compose.yml` 的 `${JWT_SECRET:-字面量}` 类兜底全部改为 `${VAR:?说明}`，`DEMO_STACK` 以字面量在基础文件钉 `true`、生产覆层显式 `false`，不为真时 API 启动即拒绝（实测输出 `refusing to start: jwt_secret is still a literal published in this repository | ...`）。`.env.example` 里 `JWT_SECRET` 与 `MEDIA_SIGNING_SECRET` 原本同为一个 `change-me-before-sharing`，已按用途拆成四个不同演示值。
- 契约测试 4 → **5** 项（新增 vendor 方言被接受 + 违约体被拒两例）；Worker Kill-9 恢复通过；双 Worker Lease 竞争通过（8 任务 / 2 claimant / 160 credits 结算一次）。
- 权限矩阵（新增量具）：`scripts/authority_matrix.py` 从 AST 派生 100 条 /api 路由、54 条写操作的权限档（这两个数是契约现值，`tests/unit/test_release_record_consistency.py` 的契约引用格会把文书里抄的每个数交回 `shared/contracts/authority-matrix.json` 重算），产出 `docs/AUTHORITY_MATRIX.md` + `shared/contracts/authority-matrix.json` 两份成对工件，`--check` 已接进 `static-verify.sh`（也就是接进 CI 的 `./scripts/static-verify.sh` 那一步）。常驻用例 7 条：派生结果必须等于 FastAPI 自己的路由表（这一条第一次跑就抓出两处读数盲区——按装饰器字符串过滤把 24 条 router 子路径全丢了、只访问 `ast.FunctionDef` 把 3 条 async 端点漏了，两者都留下了 fixture 反证）、两份成对工件不得与代码漂移、7 条无角色检查的写路由必须逐条点名其保护载体且该载体确实还写在端点源码里、`require_roles` 用到的名字必须落在 `001` 的 CHECK 词表内。5 支变异全部开火（把平台管理员检查降成工作区成员、插入一条无鉴权写路由、手改生成出来的文档、改掉矩阵里的一条路径、声称一个不存在的载体标记）。
- 备份恢复：绝对指纹校验通过（本轮读数 `2 workspaces, 2 assets byte-identical, ledgers unchanged`，计数随库里资产数变化，判据含零分母拒绝），并已用「删除 master 对象 → 判据转红 → 恢复 → 转绿」证明该校验有牙。
- 100 次生成回归：`100/100 completed, error rate 0.0%`，在册带这一步的绿线运行已有 37 次，本文逐条记下读数的依次为 p50 8.18s/p95 20.77s、7.615s/11.37s、6.25s/8.31s、8.525s/13.64s、5.195s/9.89s、7.18s/15.66s、5.09s/5.37s、4.09s/5.33s、5.06s/5.26s、6.84s/8.21s 与 `2f25fe5` 那次 5.215s/7.16s、`037b818` 那次 4.13s/5.47s、`ef88d14` 那次 4.09s/5.28s、`3dbd175` 那次（第 20 步）4.15s/8.38s、本轮（`680a0ea`，第 20 步）12.665s/19.12s，每次都是 100/100 并结算 1000 credits（计时随主机负载浮动，只能按次引用；会跨跑传的是计数不是耗时），逐任务校验哈希、结算额与账本守恒。
- **真实浏览器验收（第 22 步）**：Playwright + axe-core 4.13.0，桌面 1440 与手机 390 两档共 118 个视图记录 / 92 次 axe 扫描，**critical / serious / moderate 三档全零**（这一句由报告里的 `violations_by_impact` 盖章：那本账非空时，本格会把有数的档位逐个写成「档位 次数」；moderate 自本轮起是阻断档，`blocking_impacts` 里带着它，`minor` 留作容许档——判据不是把所有东西都否掉那句话的对照），并附 CSP、键盘可达、390px 横向溢出、「凡标了 `hidden` 的元素必须真的算不出 `display`」（本轮新增，带分母断言：整套扫描 1291 个 hidden 元素、0 个仍在渲染，一个都没见到就判红）与非捕获异常断言——报告里 `uncaught_errors` 为空。console 留痕 24 行——那是 `sorted(set(...))` 之后的**行**数（`scripts/browser_a11y.py:1668`），事件数是另一根轴：事件是另一根轴：门禁在页面上亲眼看到 53 次被拒，按标签去重成 34 条、9 个端点，最多的一支是 `GET /api/auth/me -> 401 共 28 次`；同一窗口服务端答了 51 次 4xx、摊在 8 个端点上。两份读数由 `scripts/server_refusal_census.py` 现算并对账（服务端答出的每一项必须被门禁记到，注入那一支必须只在门禁侧出现），对账件 `server-refusals.txt` 连同命令、窗口与解析行数一并落在同一个证据目录里，所以这句话不再只活在终端上。按状态分 `401 记 16 行 / 403 记 6 行 / 500 记 2 行`，每个状态各有几个标签 `401×8/403×3/500×1`；网络层自己记的 24 行、应用侧自己打的 0 行，所以「应用侧 `console.error` 归零」是从这 24 行的文本面直接读出来的，不是推出来的（见 `RELEASE_CHECKLIST.md` 附带发现第 4 条，上一轮两处都修）。401 那 16 行 = 8 个标签 × 两档视口，一个标签恰好一行：标签就是一次独立开页，所以这条读数的意思是「每次开页首屏各问一次身份」，不是某个页面刷了十六次。403 那几行压着 21 次真被拒的请求，而三行只有一支能整枝点名到写它的走查——`refusal-admin` 出自 `walk_roster_refusal`（非平台管理员读「工作区与成员」，`scripts/browser_a11y.py:1386`），它在服务端是 `/api/admin/v12/workspaces` 三次加 `/api/admin/v12/payouts` 两次（共 5 次，逐端点行见同一目录的 `server-refusals.txt`）；`axe-invitee` 那一行是受邀账户接受邀请之后（它此时是 `viewer`，不是属主）读不到属主侧那几张表：`/api/workspace/invitations`、`/api/analytics/overview`、`/api/ledger`——这条 403 是设计内路径，前端把对应区块收起来而不是报错（`services/web/app.js:2077-2078` 写明没有席位时连请求都不发，`:2090-2091` 写明有席位但无权时 403 只是把区块收起而不报错）；`axe-studio` 那一行是删除账户先用错口令的那一次（`POST /api/account/erasure` → 403，`scripts/browser_a11y.py:1000`）。本段上一版把「名册里邮箱不一致的确认」也列成拒绝之一，那是不实的：那一件由前端拦下，同一窗口 api 容器没有收到对应的写请求（`DELETE /api/workspace/members/…` 在 api 侧只出现两次，都是输对邮箱的那两次，200；web 代理那侧镜像同样的两行，不是另外两次写）。500 那 2 行是门禁自己注入的：`walk_stale_panels` 用 `route.fulfill` 把 `/api/admin/v12/moderation` 打成 500（`scripts/browser_a11y.py:1358`），要求应用不塌、那个面板自称「未能载入」、而同一次加载里另一个容器保留数字——局部失败不得被渲染成全面崩溃；`fulfill` 不出网，所以浏览器侧记到了、服务端一次也没答，这个差额本身就是该性质的证据。两侧读数现在由 `scripts/server_refusal_census.py` 现算并对账（服务端每一项必须被门禁记到，注入项必须只在门禁侧），并把结果落在同一个证据目录的 `server-refusals.txt` 里，不再只活在终端上。走查覆盖的面板状态分四组各自计数：名册与邀请 `team_states` 12 个（2026-09-26 把邀请面接进门禁时从八个变成十二个，本轮没动）、主体权利 `privacy_states` 4 个（面板、导出后的覆盖摘要、口令错的拒绝、删除回执）、两步验证 `second_factor_states` 5 个（注册中、已武装、恢复码、被拒、登录时的第二因子）、管理侧名册 `roster_states` 4 个（过期信任、工作区列表、拒绝、名册）。机器可读结果就是本段开头「权威运行」那条盖下的 `report.json`（其 `git_commit` 即被测的树），同目录另有 `server-refusals.txt` 一份服务端侧对账件——这一句上一版在这里另抄了一个 `report.json` 的路径，那一抄没人盖、也没人核，于是它停在 09-27 那一份上，而同一行的视图数与 hidden 数早已是 09-28 的读数。判据自身有牙：`--self-test` 三类注入对照 + 17 条判决函数单测。同轮这条门禁扫出并修掉一个长期缺陷（附带发现第 16 条）：`label { display: grid }` 压过浏览器默认的 `[hidden]`，于是登录页在每一个已登录视图上都叠在应用上方——上一版本文记的 moderate 96 / `region` 56 里有 48 项是它的投影。
- **对账（第 22 步内的 `scripts/server_refusal_census.py`）本轮有了第三条轴**：过去它只能说「服务端答了几次 4xx、门禁记了几次」，两边差一次时分不清是响应没交到页面手上、还是有个会话门禁压根没观察到；现在它同时读门禁自己记账的**已发起请求数**（`request_counts`，含 `api_post()` 那种页面外的自调用）与**没等到答案的请求**（`abort_timeline`），并读 `unsettled_at_close`——页面关闭前没等到 networkidle 的那几页的名字。判据仍是「服务端 ⊆ 门禁」且不加容差；降级只在运行自己交出具名 unsettled 时发生，读数缺席或为空时照旧判红。自测由 12 档长到 25 档（最后两支是收紧豁免时加的：具名页面必须等到的是**同一条端点**才降级，等待对象不相干或旧串形态一律照红）。桌面档单跑读数（`release-evidence/browser-a11y-20260928T211052Z/server-refusals.txt`，带日期的一次性诊断件）：服务端 4xx 25 == 门禁 4xx 25、`gate_issued_events: 261`、`gate_aborted_events: 5`、`unsettled_at_close: []`、对账 rc=0 且 stderr 为空；权威行以那一轮 SUMMARY 的 `metrics browser-a11y` 与其对账件为准，本文不抄它。
- **工作区名册走查（同一步新增）**：`walk_team` 在真实浏览器里把成员写入这条路走完——名册渲染 → 用一个查无此人的邮箱发出要约（必须照发不误、口令只在这一刻显示一次、名册不动、错误条必须空）→ 属主侧撤回一份还在待接受的要约（它的确认对话框里不得出现 password 输入）→ 被点名的探针在**另一个浏览器上下文**里登录，并在它自己的会话里接受（属主侧名册恰好 +1，且那条要约读作「已接受」）→ 改角色（徽标跟着变成 `reviewer`，对话框要填口令）→ 打开移出对话框、故意输错邮箱（必须拒绝且人还在）→ 输对邮箱移出（名册回到原大小），对话框本身也扫一次。12 个工作区团队面板状态两档各一次，权威运行读数 `2 roster walks with offer/accept/re-role/revoke/remove exercised`。探针账号在创建的那一刻就登记 `atexit` 清理：今天两次中断的跑法把 3 个探针留在了演示名册里，而「我建了它」必须蕴含「我删掉它」——这个钩子的可达性用一支会在 SystemExit 时开火的对照证明过（写标记文件成功），清理本身逐表容错，因为中途退出可能留下被外键指着的一行。名册面板也揪出本轮唯一一条真实无障碍缺陷：白字压在 `var(--danger)` #ff6b6b 上实测 2.78:1，axe 判 serious——它此前一直潜伏，因为危险按钮在每个被扫到的状态里都是 disabled，而 axe 不判禁用控件；两个应用改用 `--danger-solid` #b3261e（实测 6.54:1），并且这条比例现在由常驻用例直接从两张样式表算出来，不必再等某个界面恰好把它启用（见 `RELEASE_CHECKLIST.md` 附带发现第 14 条）。
- **主体权利走查（同一步新增）**：`walk_privacy` 在真实浏览器里把两条权利各走完一遍，账号是它当场建的探针（`creator` 角色、非任何工作区属主），跑完由被测功能自己删除；桌面与手机两档各一次，权威运行读数 `2 exports downloaded and parsed, 2 probe accounts erased`，导出文件 2731 / 2730 字节。每份下载都逐条校验：下载确实发生、文件名以 `account-export-` 开头、`coverage` 非空、`password_hash` 既在排除声明里也没有混进账户对象、且导出正文的 `account.id` 就是发起请求的那个会话——否则「面板导出了别人的数据」在终端上与正确形态同形。确认闸两个方向都拍：多打一个字符必须仍是禁用，打完正确邮箱必须放开，且面板显示的目标必须等于探针邮箱，不等就直接拒绝点击（这是门禁删不到演示账号的保险）。删除成功后等登录页出现回执条（`role="status"`），再用同一组凭据登录一次并要求被拒。报告键 `privacy_states`（`account-privacy-panel`、`account-privacy-exported`、`login-erased-receipt`）与 `privacy_exports` 就是「这一步没跑到」会立刻暴露的面；它不是尽力而为——探针建不起来直接判红，因为静默跳过等于把破坏性那半覆盖悄悄抹掉。新增三个状态没引入新规则：两个账户面板状态与既有 `account` 视图同形（`heading-order` + `region`），登录回执与既有 `login` 同形（`landmark-one-main` + `region`），moderate 因此 64 → 76（+12）而非冒出新的条目。
- **数据主体访问与删除演练（新增）**：`scripts/erasure_drill.py` 驱动 `GET /api/account/export` 与 `POST /api/account/erasure`（这两条通道本轮起了面向主体的界面，见下下条浏览器走查），**53 项断言**（流水线内为第 11 步，权威运行读数 53/53；独立运行亦 53/53。上一版本文记 32、本段此前记 34，两处都不再作数——同一个数在两份文档里各自漂移，正是这轮把它改成从 `information_schema` 现推的原因）。每条守卫都拍两极：不给口令→401（这是挑战，不计入爆破窗口）且库里一行未动、口令给错→403 且账号仍然 active、已武装第二因子的账号只给口令也→401 且点名要验证码、而错口令那一次不许把验证码烧掉（同一个码配上正确口令第二次就能真删，这才是「口令先判」的证据）、三次拒绝都以 `actor_id=该账号` 落进 `audit_events`（断言计数=3，不是记成 system）、确认词不匹配→409 且库里一行未动；唯一属主自删→409 且会话未被撤销；平台管理员→409 且演示属主随后仍能登录；匹配确认→会话被撤销且 `expires_at` 一字节未动、成员关系与偏好消失、身份列被假名化、再登录 401。保留性用「真去删一次、要求数据库拒绝」来证（实录 `ERROR: song_spec_revisions is immutable`）；覆盖性用 `information_schema` 里所有指向 `users(id)` 的外键列与导出自身声明的 `coverage` 求差来证，并做过正向对照（临时建 `tmp_probe_link(user_id REFERENCES users(id))` → 判据精确点名并判红，删表后转绿）。演练夹具按设计留 residue：被删除账户的假名化溯源行是 append-only，删不掉正是被测性质本身。
- **第三方 Provider 适配器往返（第 21 步）**：`docker-compose.generic-rest.yml` 把 `MUSIC_PROVIDER` 切成 `generic_rest`，让 `GenericRESTAdapter` 而不是自家模拟器适配器驱动生成；`provider_regression.py` 新增第三个参数，先看 `/api/bootstrap` 报出的身份再干活——覆层没生效就直接判红（实测：关掉覆层时报 `the stack reports 'emulator' ... the overlay did not take effect`），把端点指向死主机时报 `job_error={'type': 'ConnectError', ...}`（改前只会打 `error=None`）。在册带这一步的绿线运行已有 32 次，本文逐条记下读数的每一次都是 `25/25 completed, error rate 0.0%, settled 250 credits`，p50/p95 依次为 5.21s/6.37s（`2c3ef7f`，第 16 步）、4.14s/4.19s（`96d5955`，第 17 步）、4.14s/4.18s（`b79e70b`，第 18 步）与 4.11s/4.2s（`3da3920`，第 18 步）、5.14s/6.34s（`93b4984`，第 18 步）与 4.11s/5.23s（`99d5847`，第 18 步）、6.4s/10.17s（`2f25fe5`，第 19 步）与 4.1s/5.19s（`037b818`，第 20 步）、4.11s/4.16s（`ef88d14`，第 21 步）与 8.6s/20.91s（`3dbd175`，第 21 步）、本轮 6.26s/15.1s（`680a0ea`，第 21 步）——计数会跨跑传，耗时不会。诚实边界：端点仍是我们的模拟器，这一步证明的是适配器与契约管线，不等于真实 Provider，G9 那条仍然未闭。
- 执行中实测到并修复的真实缺陷（详表见 `docs/FINAL_RELEASE_STATUS.md` 与 `docs/RELEASE_CHECKLIST.md` 的"附带发现"）：独家资产可被重复售出、`GET /orders` 把分页行数当金额、退款后独家商品卡在 `sold`、自家 CSP 静默作废雷达配色/提示/进度条、UI 代理把重建后 API 的死地址钉住导致全量 502、错误路径重复读流导致真因被吞、管理后台工作区下拉无可访问名、390px 视口被顶栏撑到 435px、入库交付清单 `SOURCE_MANIFEST.sha256` 只有 110/188 个文件被如实描述（现由 `scripts/source-manifest.sh check` 进 `static-verify` 把关），以及本轮新增的三条导出/删除侧缺陷：导出把 `brand_submissions` 的租户列当作 `workspace_id` 导致整份导出 500；删除函数撞上 005 自家会话不可变触发器（`expires_at` 在不可变清单里），于是每一次删除都 409；`user_preferences`/`product_events` 受 FORCE RLS 保护，不带租户作用域的读静默返回空——同一 RLS 陷阱在写侧是 011/012，在读侧此前无人踩过；终态失败可以完全不带原因（13/15 个 failed 作业 `error=NULL`，真因藏在候选行里，需手工 join 才看得到）；以及两个 provider 适配器各自发明一种请求方言而仓库里没有任何写下来的 provider 契约（`generic_rest` 对我们自己的模拟器次次 422）。

仍未取得有效证据的只有一项：**500 并发容量 Gate**（本轮又在当前树上取了一支读数：2026-09-28T21:41:21Z 起，500 用户 × 2 共 1000 个请求全部 200、`error_rate=0.000%`、88.4 req/s，p50 3144.6ms / p95 **8357.5ms** / p99 9074.5ms，`gate=FAIL`、退码 2；这一支是复用当时仍在跑的栈取的热栈读数，没有先 `down -v`，与 `docker-compose.capacity500.yml` 自身对 api/worker 各 4 CPU / 4 GiB 的请求相比仍受同一台 4 vCPU 宿主限制，逐支读数与离散度见 `docs/COST_OPTIMIZED_500_CONCURRENCY.md` 与 `capacity-results/20260928T214121Z.log`）。本轮又把客户端按该文档指的路搬到宿主流量侧复跑了一支（`CAPACITY_FROM_HOST=1`，身份门先放行：唯一认领者、本站 90 条端点、端口出自本项目自己的 `docker compose port`）：`p50 3210.6 / p95 5060.6 / p99 5742.4ms`、`throughput 126.7 req/s`、`statuses={502: 102, 200: 898}`、错误率 10.2%、`gate=FAIL`；而被测网关同时段只记到 898 行 `/api/projects`、0 行 502（api 侧也 0 行）——那 102 个 502 从未进入被测栈，是宿主到 VM 的转发链路在 500 并发下丢的。两种摆位各自的红因都在读数里，不在推断里。本机 4 vCPU / 6 GiB 低于 `docker-compose.capacity500.yml` 自身对 api/worker 各 4 CPU / 4 GiB 的请求，Gate 在 500 用户下实测判红（p95 4197ms 与 9832ms 两次，错误率 0%），并发扫描与结论见 `docs/COST_OPTIMIZED_500_CONCURRENCY.md`。因此上面那句「需部署主机的运行日志才算证据」依然成立——只是现在有了真实的失败读数，而不是缺失读数。
