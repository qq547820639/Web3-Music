# 500 并发低成本生产化交付说明

## 目标边界

本交付把 v13 的代码侧生产化任务一次性收敛到“约 500 名用户同时在线使用”的容量目标。这里的 500 并发指 Web/API 同时在线和请求突发，不等于 500 个音乐 Provider 生成任务同时执行。音乐生成的真实并发上限仍由正式 Provider 合同、配额、限流和单位成本决定。

## 已完成的低成本改造

1. **API 数据库连接复用**：新增线程安全 PostgreSQL 连接池、连接获取背压、连接/语句超时，避免每个请求新建数据库连接。
2. **Worker 单进程并行**：`WORKER_CONCURRENCY` 默认 8，一个 Worker 容器可并行处理多个异步生成 Job，不再需要“一任务一容器”。
3. **Provider 连接复用与自适应轮询**：Provider/媒体下载复用 HTTP keep-alive；轮询从 3 秒逐步退避到 10 秒，单次异步等待窗口提高到 600 秒，减少无效请求，同时避免长音乐生成被过早当成失败。
4. **非阻塞媒体入库 + 媒体直出**：ffprobe 与对象存储上传移出 asyncio 主循环，多个 Job 可真正并行；生产可用 `MEDIA_DELIVERY_MODE=presigned`，让音频从对象存储直达客户端，不经过 API 容器转发。
5. **数据库索引**：增加任务领取、过期 Lease、取消任务、Workspace 列表、Inbox、步骤和额度查询索引。
6. **低成本扩缩**：提供 Kubernetes 2 个 API + 2 个 Worker 起步、API HPA 2–8、Worker HPA 2–10、PDB；避免一开始按峰值常驻资源付费。
7. **单机容量验证配置**：`docker-compose.capacity500.yml` 用于压测/预发布，不要求昂贵 Kubernetes 环境即可先完成 500 用户容量 Gate。
8. **自动容量 Gate**：`scripts/capacity-gate-500.sh` 自动启动系统、执行 500 并发认证 API 测试，再跑领域 Acceptance，防止“压得动但账本/权限错”。
9. **可观测容量指标**：Worker 新增 active/waiting/oldest-wait 和 slot busy 指标，可根据队列积压调并发或副本。
10. **修复请求 ID 响应头错误**：修复原 Middleware 的响应头赋值问题。

## 推荐最低成本生产规格

### API

- 起步副本：2
- 每 Pod：300m CPU / 384 MiB request，1 CPU / 1 GiB limit
- `API_WORKERS=1`（Kubernetes 用 Pod 横向扩容，避免多进程指标分裂）
- `DB_POOL_MAX=16`
- HPA：2–8

### Worker

- 起步副本：2
- `WORKER_CONCURRENCY=8`，初始最多约 16 个任务并行执行
- `WORKER_DB_POOL_MAX=16`
- Provider 轮询：3 秒
- 对音乐 Provider 的总并发必须按合同限额调低或提高，不应直接等同于用户并发。

### PostgreSQL

建议从 4 vCPU / 16 GiB 的托管实例压测，也可以先从更低规格验证。API 与 Worker 已有应用层连接池；若实际 HPA 后数据库连接仍成为瓶颈，再增加 PgBouncer/RDS Proxy，而不是第一天就增加常驻组件。

### Redis

只承担事件/缓存；不作为账本事实源。500 在线用户阶段可以从小型 HA 实例起步。

### 音频

生产启用对象存储 presigned URL，并配置 `S3_PUBLIC_ENDPOINT_URL` 为浏览器可访问的对象存储端点；未配置时系统安全回退到 API Proxy。若使用 CDN，应由 CDN/对象存储承担播放带宽。不要让 API 容器承担 500 人音频长连接。

## 一次性验证命令

```bash
cp .env.example .env
./scripts/capacity-gate-500.sh
```

默认执行：

- 500 个并发用户；
- 每用户 2 次 `/api/projects` 请求；
- P95 门槛 800ms；
- 错误率门槛 1%；
- 流量测试完成后继续跑 Acceptance。

调整：

```bash
CAPACITY_USERS=500 \
CAPACITY_REQUESTS_PER_USER=5 \
CAPACITY_MAX_P95_MS=800 \
KEEP_CAPACITY_STACK=1 \
./scripts/capacity-gate-500.sh
```

## 实测记录（2026-09-25，首次真实执行）

执行环境：Docker 29.5.2 / Compose v5.4.0，Colima 虚拟机 **4 vCPU / 6 GiB**（低于本文件建议的 4 vCPU / 16 GiB，也低于 `docker-compose.capacity500.yml` 自身对 api 与 worker 各 4 CPU / 4 GiB 的资源请求）。

`./scripts/capacity-gate-500.sh` 在默认 500 用户下 **判红**：`error_rate=0.000%`、1000 个请求全部 200，但 p95 两次分别实测 **4197ms** 与 **9832ms**，未过 800ms 门槛。

同一端点（经 gateway 的 `GET /api/projects`，每用户 5 次）并发扫描：

| 并发用户 | p50 (ms) | p95 (ms) | Gate |
|---|---|---|---|
| 1 | 2.0 | 2.6 | PASS |
| 10 | 36.0 | 48.5 | PASS |
| 20 | 91.3 | 115.9 | PASS |
| 25 | 138.0 | 246.0 | PASS |
| 30 | 120.6 | 180.3 | PASS |
| 50 | 617.3 | 1004.4 | FAIL |
| 100 | 1379.3 | 1896.4 | FAIL |
| 200 | 1697.2 | 1951.4 | FAIL |
| 400 | 2426.1 | 3308.9 | FAIL |
| 500 | 5374.4 | 9832.3 | FAIL |

读数解释（不作为通过依据）：单请求成本约 2ms，延迟随并发近似线性增长、吞吐在约 130-160 req/s 之后不再上升，属于**排队**而非该端点存在病态实现。本机资源不足以证明 500 并发达标，因此**500-user 容量结论仍未取得**，需在达标主机或 CI 大规格 runner 上重跑。上一版这里写过「调参前不应以这份数据为依据改动 `API_WORKERS`／连接池规模」——该留观本轮已由下面的受控对照做掉，并顺带查出容量覆层的一个配置缺陷。

### 2026-09-27 受控对照：容量覆层的 `API_WORKERS` 从来没生效过

**缺陷。** `docker-compose.capacity500.yml` 把并行度写成 `API_WORKERS: ${API_WORKERS:-4}` 这类「带缺省的插值」。Compose 的插值取自宿主环境 + `.env`，而跑这套栈的每台机器都有 `.env`（CI 与 `capacity-gate-500.sh` 都会 `cp -n .env.example .env`，api/worker 容器又 `env_file: [.env]`），`.env.example` 里是 `API_WORKERS=1`。结果：覆层自己声明的 4 从未进过容器——`docker compose exec -T api sh -c 'echo $API_WORKERS'` 读回 **1**，而文件写着 4。现在覆层的数字改成字面量，同一命令读回 **4**，容器日志里 `Started server process` 恰好 4 行（这条判据的常驻版在 `tests/unit/test_environment_contract.py`，另有一条控制用例专门让它对旧形状开火）。上表所有历史读数，包括 `capacity-gate-500.sh` 判红那两次，都是在**单 worker** 下取到的。

**受控对照（本机 4 vCPU 虚拟机，同一套栈、同一个全新库，只改 worker 数，每支都重建 api 并重复取数；端点为经 gateway 的 `GET /api/projects`）：**

| 形状 | 1 worker（两次） | 4 worker（两次） |
|---|---|---|
| 128 用户 × 5 | p50 1089.9 / 1091.5ms；p95 1374.9 / 1391.1ms；111.9 / 110.9 req/s | p50 577.3 / 723.2ms；p95 1152.0 / **4517.9**ms；167.2 / 89.9 req/s |
| 500 用户 × 5 | p50 6501.0ms；p95 15427.3ms；57.1 req/s | p50 2106.3ms；p95 15747.8ms；102.7 req/s |
| 500 用户 × 2（Gate 的形状） | p50 7271.8 / 6375.4ms；p95 11726.6 / 10697.4ms；59.4 / 65.4 req/s | p50 3508.8 / 5000.2 / **5445.8**ms；p95 10500.5 / 12640.4 / 11433.9ms；73.8 / 69.5 / 77.7 req/s |

**能下的结论与不能下的结论。** 只有一件事在两两重复里稳定：中位延迟随并行度下降（128×5 约 -34%~-47%，500×5 -68%，500×2 约 -22%~-52%）；p95 与吞吐不稳定——128×5 的 4 worker 一支给出 p95 1152.0ms、另一支给出 **4517.9ms**，而 1 worker 两支都在 1375~1391ms。第一次单独取到的「4 worker 下 128 用户 p95 611.8ms、可过 800ms 门槛」在读第二遍时**没有复现**，因此该结论撤回，500 并发与 128 并发的 p95 门槛在本机都没有通过证据。修好配置的实际意义是：此后 Gate 的红是「按覆层自己声明的 4 worker 跑出来的红」，而不是「按被 `.env` 换掉的 1 worker 跑出来的红，再记在宿主容量账上」。

**为什么这台机器取不到稳定的尾延迟（读数，不作为推断）：** 一次 500 用户压测期间 `docker stats --no-stream` 给出 `loadtest` 68.79%、`api` 71.07%、`postgres` 89.28% CPU——客户端、服务与数据库在同一个 4 vCPU 虚拟机里抢核（`capacity-gate-500.sh` 用 `--base-url http://gateway`，压测容器就跑在被测栈旁边）。要拿到能签字的 p95，客户端必须移到被测资源之外（宿主上按 `http://127.0.0.1:8080` 打，或达标主机／CI 大规格 runner），并把同一形状重复 ≥3 次报出离散度。

**「把客户端移出虚机」在本机试过，走不通（2026-09-27，读数留档）。** 网关在宿主发布在 `127.0.0.1:18080`（`GATEWAY_PORT=18080`），从宿主用同一把 `scripts/load-test-500.py` 直打：500 用户 × 2 的一轮给出 **error_rate=99.800%**——`statuses={500: 81, 200: 2, 'ReadTimeout': 917}`，p50 23862.6ms。api 容器日志里同窗口的真是原因：**610 条** `psycopg2.OperationalError: database pool acquisition timed out`（池获取 8 秒超时被当成未处理异常 → HTTP 500 + traceback）。已证实的是这两件事：转发路径下池耗尽会以 500 的形式冒出来；同栈同形状经容器网络（`--base-url http://gateway`）三轮 0% 错误。**推断（未证实）**：Colima 发布端口这层转发把 keep-alive 连接打碎、每个请求重建连接，才把池等待推过 8 秒——所以「宿主直打」量到的是转发路径，不是这套栈；要拿到干净的客户端在外的读数，只能用第二台机器或 CI 大规格 runner（上文已登记的外部阻塞）。顺带留给属主定值的一件事：池耗尽今天映射为 500 + traceback 洪水（610 条），按语义更像应当 503 + Retry-After；本机 Gate 的容器网络路径碰不到它，生产部署在真实 NAT/LB 后面会碰到，改不改属产品语义决定。

**第三支读数来自改过的量具本身（2026-09-27，上表加粗处）。** `capacity-gate-500.sh` 原先用 `up --build -d <11 个服务>` 一次性并发建镜像——正是 `step_stack_up` 已实测过会在这台 4 vCPU 机器上把无关服务的 `apt-get` 打成 SIGKILL（rc 137）的形状——现改为按 `compose config --profile '*' --format json` 里真正带 `build` 段的服务逐个 `build` 再 `up -d`：整轮日志里 8 个镜像逐个 `Building/Built`，`postgres`/`redis`/`minio` 这三个纯镜像服务被正确排除，脚本自带的 `static-verify` 先过（331 passed），随后同一形状取到 p50 5445.8ms / p95 11433.9ms / 77.7 req/s、`error_rate=0.000%`、`gate=FAIL`。**该轮的局限要说清**：Gate 判红即退出（`set -e` + trap 清栈），所以「压完再跑一遍领域 Acceptance」那一步这次没有执行，本轮只证明了脚本改造后仍能跑到判据并给出读数，没有证明红后复跑链路。

同轮一并观察（**本条已由读码 + 实测更正**）：`LOGIN_RATE_LIMIT_PER_MINUTE` 缺省 10 次/分钟，原先记为"按 IP 计"是误读——键实为 `login:sha256(email)`（`services/api/app/main.py:162`），即**按账号**，同一出口 IP 后的用户不会互相挤爆；实测第 11 次尝试起 429。真正的两个问题是：限流脚本对每次 INCR 都无条件 `EXPIRE 60`（含被拒的那次），窗口随敲门频率滑动，实测"每 30 秒敲一次"连 90 秒都进不去、直到静默满 75 秒才恢复；以及缺少跨账号维度，单账号可被知道邮箱者持续锁在门外，而分散撞库不受约束。本轮未动服务端缺省值，只把测试客户端退避改成 ≥75 秒（原来的 12–30 秒退避会被自己饿死），定值权留给属主。

**2026-09-27 同一天的后续（本节记的是改前形状，保留不动）**：上面那两个问题都已定值并做完。滑动窗改成了固定窗口（`_login_gate` 只读、`_login_failed` 只在凭据失败那一支花额度），跨账号维度也有了——登录多了一道按**来源地址**计的去重集合窗口（`LOGIN_SOURCE_RATE_LIMIT_PER_MINUTE=20` 个不同账号/分钟），它成立的前提是 api 能拿到可信地址，而这要先做两处部署改动：网关覆盖 `X-Forwarded-For: $remote_addr`（`services/gateway/nginx.conf:43`、`:61`）、compose 把网关钉成静态地址并让 uvicorn 只信那一个（`docker-compose.yml:120`、`:251`、`services/api/Dockerfile:16`）。同一轮里 `auth_sessions.ip_hash` 从「对客户端 IP 取不加盐摘要」改成带 `ADDRESS_PEPPER` 的摘要，因为地址的全域只有 2**32，而不加盐那版当时记的还一直是网关容器。三处 75 秒退避随之降到 61 秒。取证、变异对照与仍未闭的一面全在 `docs/RELEASE_CHECKLIST.md`「已修」第 24 条，本节不再重复读数。

## 压测器选型记录（2026-09-25）

当前 Gate 用的是仓内自研 `scripts/load-test-500.py`（asyncio，124 行，已自带
`--max-p95-ms` / `--max-error-rate` 判据并以非零码退出）。按选型规程对外部成熟实现做了比对，
候选为 Grafana k6 与 Locust：

| 维度 | 保持自研 | k6（grafana/k6） | Locust |
|---|---|---|---|
| 功能匹配度 | 已满足单端点 SLO Gate + 与 Compose 同网络起栈 + 压后跑 Acceptance | 阈值原生且更强：官方文档给出 `p(95)<800`、`rate<0.01` 这类判据，阈值不过则"test finishes with a failed status"并以非零码退出；支持 ramping-vus、分布式执行 | 面向可编程用户行为，Python 生态一致；但本次在其项目文档页未见内建的 SLO 阈值/非零退出机制（文档本身未记录，不等于运行时一定没有） |
| License 兼容性 | 无新增依赖（本仓为 MIT） | README 明示 `AGPL-3.0`；作为 CI/开发期独立二进制调用不构成对应用代码的传染，但不得把 k6 代码 vendored 进服务 | MIT（其文档页明示），约束最松 |
| 维护活跃度 | 由本项目维护，规模小 | 31,579 stars、非归档、比对当日（2026-09-25）仍有更新 | 活跃（本次未取星数等指标，不作结论） |
| 安全风险 | 攻击面最小，仅 httpx/aiohttp 级别 | 引入 JS 运行时与 Go 二进制；脚本即依赖 | 引入 Python 依赖树与 Web UI（默认监听需关闭） |
| 代码质量 | 手写分位数/统计，已有回归覆盖 | 成熟、统计口径久经使用 | 成熟 |
| 适配成本 | 0 | 需用 JS 重写场景；要复刻当前"压完立刻跑领域 Acceptance"的编排（该编排价值高于压测本身） | 需把认证 + CSRF 双提交与 think-time 语义改写成 Locust task |

**决定：本轮不替换压测器。** 理由是功能与判据能力上外部方案没有补上当前 Gate 缺的东西——
p95/错误率阈值与非零退出自研已具备；而本轮实测到不达标的真正原因是宿主资源
（4 vCPU / 6 GiB < profile 自身请求的 4+4 GiB），换工具不会改变这一结论。k6 值得在未来
需要「多端点 SLO 矩阵 / 阶梯爬坡 / 分布式发压」时引入，届时借鉴其 thresholds 声明式写法。

核实来源（本轮实际访问）：
- Grafana k6 官方文档 thresholds 页：`https://grafana.com/docs/k6/latest/using-k6/thresholds/`
- k6 README 许可声明：`https://raw.githubusercontent.com/grafana/k6/master/README.md`
- k6 仓库元数据（语言/星标/更新时间）经 GitHub API 查询 `grafana/k6`
- Locust 官方文档 `what-is-locust` 页（许可为 MIT）

未核实项（不作为结论依据）：Locust 的实际维护指标与 k6 的性能基准数据。

## 生产部署

Kubernetes 基线：

```bash
kubectl apply -f infrastructure/kubernetes/resonance-apps.yaml
kubectl apply -f infrastructure/kubernetes/resonance-capacity-500.yaml
```

先独立执行 Migration Job，再滚动 API/Worker。`resonance-capacity-500.yaml` 是容量覆盖层；正式环境还需由云平台配置 Ingress/WAF/TLS、托管 PostgreSQL/Redis/S3、Secret Manager 和日志/Trace。

## 仍不能由代码包代替的外部事项

以下不是继续“开发功能”，而是上线证据，无法在没有真实账号/合同的离线交付环境中伪造：

- 正式音乐 Provider API Key、并发额度、商业合同与真实 100+ 回归；
- 真实支付商户、退款/税务/发票；
- 正式 OIDC/MFA/企业 SSO；
- 云账号中的 KMS、WAF、TLS、备份/PITR；
- 在最终云环境执行本文件的 500 并发 Gate、故障恢复和独立安全测试。

因此，代码侧可以一次性交付；最终的“500 并发已通过”只能在实际部署环境跑完容量 Gate 后签字。
