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

2026-09-26 更新：跨容器 E2E、Provider/Payment 契约、Worker Kill-9 与双 Worker Lease 竞争、备份恢复绝对指纹、商业全链路、跨租户隔离、100 次生成回归、账户导出/删除、第二因子、成员角色三条常驻演练，以及**真实浏览器 + axe 无障碍/移动端验收**，已在真实 Compose 栈执行并留证（权威运行 `release-evidence/acceptance-20260926T075659Z/`，commit `93b4984`，20 行里 19 步 PASS + capacity 按开关跳过；前序 9 次 `FAIL=0` 为 `82f2ffe`→`5028686`→`28deafc`→`1d8534e`→`01e61d1`→`2c3ef7f`→`96d5955`→`b79e70b`→`3da3920`，行数 15→20，套件每轮在变宽；四次判红保留为发现记录：`acceptance-20260925T223656Z`、`acceptance-20260925T223811Z`、`acceptance-20260926T021711Z`、`acceptance-20260926T022643Z`；浏览器结果 `release-evidence/browser-a11y-20260926T080723Z/report.json`，见 `docs/FINAL_RELEASE_STATUS.md`）。

仍未取得、也不得以本机结果签字的证据：真实 500 用户压测（本机 4 vCPU / 6 GiB 低于该 profile 自身资源请求，Gate 实测判红，见 `docs/COST_OPTIMIZED_500_CONCURRENCY.md` 实测记录）、500 同时音乐生成、正式 Provider 合同容量、云数据库故障转移、独立渗透测试，以及正式支付与目标云账号相关项。
