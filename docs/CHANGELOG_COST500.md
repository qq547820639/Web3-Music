# v13.0.0-cost500 增量变更

本包以用户提供的 v13 Final 为基线，只做容量、成本、可靠性和部署收敛，不扩张产品范围。

## 代码

- API：PostgreSQL ThreadedConnectionPool + 获取背压 + statement/connect timeout。
- API：修复 Middleware `X-Request-Id` 响应头赋值。
- API：DeepSeek HTTP keep-alive 复用。
- API：S3 client 缓存；可选 presigned 音频直出，未配置公网端点时自动回退 API Proxy。
- API：Provider Capability Snapshot 进程内缓存，配置变更随发布重启生效。
- Worker：单进程 `WORKER_CONCURRENCY` 并行 Job slots。
- Worker：Provider HTTP client 与媒体下载 client 复用。
- Worker：Provider 自适应轮询 3s→10s，poll window 默认 600s。
- Worker：ffprobe 和 S3 upload 从 asyncio 主循环移到线程，避免阻塞其它 Job。
- Worker：音频对象 key 改为基于 SHA-256 的确定性路径，降低并发重复对象/孤儿文件。
- Worker：Provider adapter version 与 capability snapshot 对齐。
- Worker：新增 active/waiting/oldest queue age/slot busy 指标。
- DB：新增 006 容量索引迁移。
- Gateway：keepalive 和共享 NAT 突发容量调优。

## 部署与验证

- `docker-compose.capacity500.yml`：单机预发布/容量验证覆盖层。
- `scripts/load-test-500.py`：500 并发异步 HTTP Gate。
- `scripts/capacity-gate-500.sh`：一键启动、500 并发认证 API 压测、Acceptance 回归。
- `infrastructure/kubernetes/resonance-capacity-500.yaml`：2 API + 2 Worker 起步、HPA/PDB。
- `services/loadtest/Dockerfile`：无需宿主机安装 Python 依赖即可跑容量测试。
- 静态验证扩展到 capacity Compose 和 Kubernetes YAML。
- 新增容量包回归测试；最终单元测试 28/28 通过。

## 未伪造的外部证据

本节按下述时间线记录：写作当时交付打包环境没有 Docker daemon，因此跨容器 E2E 未被宣称通过（这一保留是正确的做法，未被用静态验证冒充）。

2026-09-27 更新（本条目 2026-09-26 首次写下，下面的读数按本轮权威运行重算）：跨容器 E2E、Provider/Payment 契约、Worker Kill-9 与双 Worker Lease 竞争、备份恢复绝对指纹、商业全链路、跨租户隔离、100 次生成回归、账户导出/删除、第二因子、成员角色三条常驻演练，**真实浏览器 + axe 无障碍/移动端验收**，以及破坏性自助动作的当场再认证（口令，必要时外加验证码），已在真实 Compose 栈执行并留证（权威运行 `release-evidence/acceptance-20260928T230940Z/`，commit `01147cf`，全新数据库（本轮起 `FRESH=1` 让流水线自己清库，SUMMARY 头部记 `fresh_database=1`），启动时宿主 `host_load="4.72 4.80 4.86"`、Docker VM 4 vCPU，23 行里 22 步 PASS + capacity 按开关跳过；前序 30 次 `FAIL=0` 为 `82f2ffe`→`5028686`→`28deafc`→`1d8534e`→`01e61d1`→`2c3ef7f`→`96d5955`→`b79e70b`→`3da3920`→`93b4984`→`99d5847`→`1f19952`→`414752d`→`aa3605b`→`fc70d13`→`cb8b901`→`cc63bee`→`2f25fe5`→`037b818`→`ef88d14`→`3dbd175`→`1dbf99e`→`fb6ff39`→`a841628`→`d98e650`→`c9e3cbd`→`c007b21`→`c469bf3`→`e75dcc6`→`e699e60`，行数 15→23，套件每轮在变宽；27 份判红 SUMMARY 留档（26 年 09 月 25 日 7 份、26 年 09 月 26 日 8 份、26 年 09 月 27 日 5 份、26 年 09 月 28 日 7 份），下列是逐条解释过的：`acceptance-20260925T223656Z`、`acceptance-20260925T223811Z`、`acceptance-20260926T021711Z`、`acceptance-20260926T022643Z`、`acceptance-20260926T093142Z`（这一条是浏览器门禁扫出「标了 hidden 的元素其实还在渲染」，见 `docs/FINAL_RELEASE_STATUS.md` 同日一节）、`acceptance-20260927T030055Z`（第 6 步 lease-contention 红：构建循环只枚举 `docker compose config --services`，profile 后面的 `worker-b` 从未被重建，那台旧镜像把字面量 `clean` 写进 `media_assets.scan_status`，被迁移 020 的触发器拒收、作业落成 `partial`，消息却报在 lease 上；修复 `d9687d8`：循环改从 `docker compose --profile '*' config --format json` 取服务，提交作业前比对两个 claimant 容器内 `/app/worker.py` 与宿主 `services/worker/worker.py` 的哈希）与 `acceptance-20260927T035001Z`（第 5 步 chaos-worker-recovery 红 "expired lease was not reclaimed by the restarted worker"：那条 lease 已被回收，回收者是 18 分钟前手工起的 `worker-b`（`generation_jobs.lease_owner` = `worker-0a906393`，仍在心跳），不是本步杀掉、自称 `worker-e02a9f18` 的那个 worker；修复 `cb8b901`：该步进入时移除 `worker-b` 并要求 compose 只报一个名字以 `worker` 开头的运行中服务，失败消息带上最后读到的 status 与 attempt 计数）；浏览器结果 `release-evidence/browser-a11y-20260928T232127Z/report.json`）。

仍未取得、也不得以本机结果签字的证据：真实 500 用户压测（本机 4 vCPU / 6 GiB 低于该 profile 自身资源请求，Gate 实测判红，见 `docs/COST_OPTIMIZED_500_CONCURRENCY.md` 实测记录）、500 同时音乐生成、正式 Provider 合同容量、云数据库故障转移、独立渗透测试，以及正式支付与目标云账号相关项。
