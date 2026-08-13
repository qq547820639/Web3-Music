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

交付环境没有 Docker daemon、正式音乐 Provider、正式支付和目标云账号，因此没有宣称以下证据已通过：跨容器 E2E、真实 500 用户运行压测、500 同时音乐生成、Provider 合同容量、云数据库故障转移和独立渗透测试。对应命令和 Gate 已随包交付，应在目标 Staging 执行后签字。
