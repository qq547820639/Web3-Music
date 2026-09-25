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

读数解释（不作为通过依据）：单请求成本约 2ms，延迟随并发近似线性增长、吞吐在约 130-160 req/s 之后不再上升，属于**排队**而非该端点存在病态实现。本机资源不足以证明 500 并发达标，因此**500-user 容量结论仍未取得**，需在达标主机或 CI 大规格 runner 上重跑；调参前也不应以这份数据为依据改动 `API_WORKERS`/连接池规模。

同轮一并观察：`LOGIN_RATE_LIMIT_PER_MINUTE` 缺省 10 次/分钟且按 IP 计，串行执行的验收步骤会被它挡到 429（本轮通过让脚本退避解决，未放宽该控制）。真实站点若用户共用出口 IP，这一缺省值会误伤正常登录，需要属主定值。

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
