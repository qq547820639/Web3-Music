#!/usr/bin/env bash
#
# acceptance-all.sh — 一键 E2E 验收管线（Resonance AI 音乐资产平台）
#
# 在部署主机（具备 Docker + Compose v2）上运行，按固定顺序串联现有的验证脚本：
#   static-verify → compose 起栈 → acceptance(test.sh) → contract-test →
#   chaos-worker-recovery → lease-contention → restore-fidelity → backup/restore →
#   复跑 acceptance → commercial-flow(commercial-test 覆层) → reservation-race →
#   market-reconciliation → hold-drill(Legal Hold 真实覆盖面) → provider-regression →
#   generic-rest-roundtrip(generic_rest 适配器覆层) → browser-a11y(BROWSER=1) →
#   capacity-gate-500(CAPACITY=1)
#
# 每步打印醒目的分节与时间戳；任一步失败即打印诊断并 exit 1。
# 全部通过后，将步骤摘要与 `docker compose logs --no-color` 归档到
#   release-evidence/acceptance-<时间戳>/
#
# 注意：本脚本的 shebang 为 bash，但为兼容 CI 里的 `sh -n` 静态校验，
# 全程保持 POSIX sh 语法（不依赖数组、[[ ]]、local、process substitution）。
set -euo pipefail

cd "$(dirname "$0")/.."

# 环境预检：缺少解释器或 Docker 时直接退出，且不生成证据目录。
# 早先版本让第 1 步自己失败，于是落盘一份「static-verify FAIL」的证据，
# 它记录的是跑错 shell（venv 不在 PATH）而不是被测代码，容易被误读成验收结论。
# sha256sum 与 xargs 也进了这个清单，因为它们是第 1/6/8 步在宿主上调的外部二进制：
# 实测 2026-09-27T03:36:31Z，一条不含 /sbin 的 PATH 让第 1 步先打
# 「xargs: sha256sum: No such file or directory」，再打「tracked=0 listed=229 stale=229」，
# 这份证据读起来像清单坏了，而真正坏的是跑它的 shell。
for tool in python node docker sha256sum xargs; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "预检失败：PATH 上没有可执行的 $tool。请先激活运行环境，把 venv 的 bin 目录放到 PATH 前面再运行。" >&2
    exit 2
  }
done
docker info >/dev/null 2>&1 || {
  echo "预检失败：docker daemon 不可访问，跨容器验收无法执行。" >&2
  exit 2
}

# 磁盘余量预检。这条链要在同一台虚机里跑 100 次生成、备份与恢复，写盘满了不会以「环境红」的样子
# 出现，而是以产品缺陷的样子出现：实测 2026-09-27T06:55:07Z（权威链第 12 步 mfa-drill）报的是
# 「未武装账号仍可用口令登录 —— 500 Internal Server Error」，而 api 容器里的真因是
# `psycopg2.errors.DiskFull: could not extend file "base/18221/18380"`，当时 `df /` 只剩 718780 KB。
# 阈值由那次失败的位置往上抬：708 MB 是实测会死的量，缺省 4 GiB 是「离死还远到可以开始」。
# 测不到就写 unknown 并继续——unknown 不等于通过，读数本身进 SUMMARY 头部。
MIN_FREE_KB=${MIN_FREE_KB:-4194304}
probe_image=$(docker compose --profile '*' config --format json 2>/dev/null \
  | python -c 'import json,sys; print((json.load(sys.stdin).get("services") or {}).get("postgres", {}).get("image") or "")' 2>/dev/null)
disk_free_kb=unknown
if [ -n "$probe_image" ] && docker image inspect "$probe_image" >/dev/null 2>&1; then
  disk_free_kb=$(docker run --rm --entrypoint df "$probe_image" / 2>/dev/null | awk 'NR==2 {print $4}')
  [ -n "$disk_free_kb" ] || disk_free_kb=unknown
fi
if [ "$disk_free_kb" != "unknown" ] && [ "$disk_free_kb" -lt "$MIN_FREE_KB" ]; then
  echo "预检失败：Docker 数据盘只剩 ${disk_free_kb} KB，低于阈值 ${MIN_FREE_KB} KB。" >&2
  echo "本轮实测：docker image prune -f 报回收 3.671 GB 后，同一挂载的余量从 718780 KB 升到 16915364 KB。" >&2
  echo "清出空间后重跑；确要在小盘上跑请显式给 MIN_FREE_KB=<更小值>。" >&2
  exit 2
fi
echo "disk_free_kb=$disk_free_kb min_free_kb=$MIN_FREE_KB probe_image=${probe_image:-none}"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_DIR="release-evidence/acceptance-${STAMP}"
RESULTS_FILE="${EVIDENCE_DIR}/SUMMARY.txt"
COMPOSE_LOG="${EVIDENCE_DIR}/compose-logs.txt"
COMMERCIAL_LOG="${EVIDENCE_DIR}/commercial-compose-logs.txt"

mkdir -p "$EVIDENCE_DIR"

# 步骤序号，供 step()/run_step() 使用，并在 SUMMARY 中作为证据来源。
step_num=0

# host_load_reading：把宿主压力写进证据头，这样跨容器步骤的失败可以先归因、再决定是不是代码问题。
# 必要性是实测出来的：这台机器的 Docker VM 只有 4 个 vCPU，2026-09-26 一次宿主 load 29-36 的运行里，
# provider emulator 内部 10s 的阻塞被放大成客户端 20s 读超时，同一棵树两次绿、第三次红在同一步骤；
# 而当时 SUMMARY 里没有任何一行能说明"跑在什么上"。
host_load_reading() {
  load=$(sysctl -n vm.loadavg 2>/dev/null | tr -d '{}' | tr -s ' ' | sed 's/^ //;s/ $//')
  [ -n "$load" ] || load=$(awk '{print $1, $2, $3}' /proc/loadavg 2>/dev/null)
  host_cpus=$( (sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null) )
  docker_cpus=$(docker info --format '{{.NCPU}}' 2>/dev/null)
  echo "host_load=\"$load\" host_cpus=${host_cpus:-unknown} docker_cpus=${docker_cpus:-unknown}"
}

# 初始化 SUMMARY 头。
{
  echo "Resonance AI Music Asset Platform — E2E Acceptance"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "evidence_dir=$EVIDENCE_DIR"
  echo "host=$(hostname 2>/dev/null || echo unknown)"
  echo "$(host_load_reading)"
  echo "git_commit=$(git rev-parse HEAD 2>/dev/null || echo unavailable)"
  echo "fresh_database=${FRESH:-0}"
  echo "disk_free_kb=$disk_free_kb"
  echo ""
  echo "STEP | RESULT | STARTED_AT | FINISHED_AT"
} > "$RESULTS_FILE"

# step() 打印醒目的分节头与时间戳。
step() {
  step_num=$((step_num + 1))
  echo ""
  echo "================================================================================"
  echo "STEP ${step_num}: $1"
  echo "TIMESTAMP: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "================================================================================"
}

# run_step <名称> <函数名>：在“当前 shell”内执行函数（保留 set -e 的首错即停语义），
# 输出重定向到分步日志，结束后回显到控制台；失败则写摘要、收证据并 exit 1。
run_step() {
  name="$1"
  func="$2"
  step "$name"
  # 仅对步骤名做文件名安全化（空格/斜杠→下划线），目录前缀保持不变。
  safe_name=$(printf '%s' "$name" | tr ' /' '__')
  log="${EVIDENCE_DIR}/step-${step_num}-${safe_name}.log"
  t0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  set +e
  ( set -e; "$func" ) > "$log" 2>&1
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    result=FAIL
  else
    result=PASS
  fi
  t1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '%s | %s | %s | %s\n' "$name" "$result" "$t0" "$t1" >> "$RESULTS_FILE"
  echo ""
  echo "---- ${name} 输出（完整日志：${log}） ----"
  cat "$log"
  echo ""
  echo "STEP ${step_num} RESULT: ${result}"
  if [ "$result" = FAIL ]; then
    # 冒号而非竖线：SUMMARY 的表格行按 ' | ' 四列被读数脚本解析，失败行不能混进那个分母。
    printf 'failed_step_env: %s\n' "$(host_load_reading)" >> "$RESULTS_FILE"
    echo "该步骤失败，日志在 ${log}（证据目录 ${EVIDENCE_DIR}）" >&2
    finish_evidence
    exit 1
  fi
}

# finish_evidence 汇总结束时间并尽力收集 compose 日志（失败不中断）。
finish_evidence() {
  echo ""
  echo "================================================================================"
  echo "收集验收证据"
  echo "================================================================================"
  {
    echo ""
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    host_load_reading
  } >> "$RESULTS_FILE"
  if docker compose logs --no-color > "$COMPOSE_LOG" 2>&1; then
    echo "已归档 compose 日志: $COMPOSE_LOG"
  else
    echo "WARN: docker compose logs 失败（可能尚未起栈），跳过" >&2
  fi
  if docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml logs --no-color > "$COMMERCIAL_LOG" 2>&1; then
    echo "已归档商业覆层日志: $COMMERCIAL_LOG"
  else
    echo "WARN: 商业覆层 compose logs 失败，跳过" >&2
  fi
  echo "证据目录: $EVIDENCE_DIR"
}

# ---- 各步骤实现（仅编排，不重复实现既有脚本逻辑） ----

# build_images_in_order [compose 文件参数...]：把「带 build 段」的服务一个一个建，然后交给 up。
# 不用 `up --build`，两个理由都是实测来的：
#   * 并发。Colima 那台 4 vCPU / 5.8 GiB 的虚机里，`up --build` 同时拉起全部镜像，权威运行
#     acceptance-20260926T161826Z 第 2 步里 worker 的 `apt-get install ffmpeg` 与另两个镜像的 pip
#     下载挤在同一时刻，apt 被 SIGKILL（退码 137）。峰值并发本身就是放大项，排成一条队即可消掉。
#   * 盲区。`docker compose config --services` 默认不报带 profiles 的服务，于是 worker-b（contention
#     档）永远不会被重建——权威运行 acceptance-20260927T030055Z 第 6 步就是这个形状：worker-b 跑的是
#     几小时前的镜像（/app 里没有 media_scan.py），按旧代码把 'clean' 当字面量写库，被 020 的触发器
#     当场拒掉，一个作业 partial，而报出来的是一句关于租约的红。所以这里读的是
#     `--profile '*' config --format json`，再按「有没有 build 段」过滤：纯镜像服务（clamav 在这台
#     arm64 宿主上没有可用清单）不在这里 pull，交给按 profile 自便的部署方。
# 三个用覆层起栈的步骤（stack_up / commercial / generic-rest）都走这里，不再各自持有 `--build`。
build_images_in_order() {
  local service
  for service in $(docker compose "$@" --profile '*' config --format json \
      | python -c 'import json,sys; print(" ".join(sorted(n for n, s in json.load(sys.stdin)["services"].items() if s.get("build"))))'); do
    docker compose "$@" --profile '*' build "$service" || return 1
  done
}

step_static_verify() {
  ./scripts/static-verify.sh
}

step_stack_up() {
  # FRESH=1 先把卷连同容器一起清掉。这不是省事：记录里那句「权威运行跑在全新数据库上」此前只是
  # 文档对操作者的口头要求，脚本既不执行也不留读数，所以一次忘了清栈的运行和一次清过栈的运行在
  # SUMMARY 里长得一模一样。本轮就撞上了这个区别：03:53 那次第 5 步红在 `expired lease was not
  # reclaimed`，而库里那条租约正被 18 分钟前手工起起来的 worker-b 心跳着。默认仍是 0（不清卷），
  # 因为这套栈同时是开发用的栈；认证 run 显式带上 FRESH=1，读数写进 SUMMARY 头部。
  if [ "${FRESH:-0}" = "1" ]; then
    docker compose --profile '*' down -v --remove-orphans
  fi
  build_images_in_order
  docker compose up -d
  docker compose ps
  # 时钟前提：认证令牌是在 api 进程里签发的，`exp`/`nbf` 用的都是容器时钟。宿主睡过一觉之后
  # Docker VM 的时钟会落到宿主后面（本轮实测过 6 分 06 秒的差：`GET /api/account/export` 打出的
  # `exported_at` 是 2026-09-27 22:59，而同一时刻 `date -u` 已是 2026-09-28 05:05），而 VM 会在
  # 运行途中被校正——一次前跳就把刚铸出来的令牌当场变成过期件。第 11 那次就是这样红的：
  # 同一个令牌一秒前还能过 `get_user`，一秒后只能得到 401，而报出来的是一句
  # "invalid or expired access token"，读起来像认证出了缺陷。宁可在这里判红并带上读数。
  host_now=$(date -u +%s)
  for probe in api postgres; do
    ctr=$(docker compose ps -q "$probe" | head -1)
    [ -z "$ctr" ] && continue
    if ! ctr_now=$(docker exec "$ctr" date -u +%s 2>/dev/null); then
      echo "!! $probe 的时钟读不出来（容器里没有 date？），无法核对认证前提" >&2
      exit 1
    fi
    skew=$(( ctr_now - host_now ))
    [ "$skew" -lt 0 ] && skew=$(( -skew ))
    echo "clock skew vs host: $probe ${skew}s"
    if [ "$skew" -gt 120 ]; then
      echo "!! $probe 与宿主相差 ${skew}s（>120s）：Docker VM 的时钟会在运行中被校正，" \
           "而一次跳变会把刚签发的访问令牌当场变成过期件——先重启 Docker（或 docker compose down -v 后重起）再认证" >&2
      exit 1
    fi
  done
}

step_acceptance() {
  # 等价于 acceptance E2E：显式构建 acceptance 镜像后运行（此时栈已在 step_stack_up 就绪）。
  ./scripts/test.sh
}

step_contract_test() {
  ./scripts/contract-test.sh
}

step_chaos() {
  ./scripts/chaos-worker-recovery.sh
}

step_lease_contention() {
  ./scripts/lease-contention.sh
}

step_fidelity_snapshot() {
  python scripts/restore_fidelity.py snapshot
}

step_fidelity_compare() {
  python scripts/restore_fidelity.py compare
}

step_backup_restore() {
  ./scripts/backup.sh acceptance
  RESTORE_CONFIRM=YES ./scripts/restore.sh backups/acceptance
}

step_acceptance_rerun() {
  docker compose --profile test run --rm acceptance
}

step_erasure_drill() {
  python scripts/erasure_drill.py
}

step_mfa_drill() {
  # 两步验证的整条路径：暂存待确认的种子、证明已知后才武装、待用凭据不能当访问令牌用、
  # 重放被拒、恢复码一次性、窗口过期、关闭需当前码、换种子后旧恢复码失效、以及码猜测的固定窗口。
  # 慢在两处真实等待：验证码每 30 秒换一码，限流窗口要等它自己过去才能测「解除」而非「永久锁」。
  python scripts/mfa_drill.py
}

step_member_drill() {
  # 工作区成员与角色的写入路径（G11）：加人、改角色、移人、移交所有权，四条写入全部走 017 的
  # SECURITY DEFINER 函数；演练同时用 SET ROLE music_app 直接试着写这张表，要求数据库拒绝——
  # 这才是「应用层改不动权威成员表」的实证，而不是注释里的一句承诺。
  # 它还闭合了删除权的一条旧死路：014/016 的擦除守卫说「先移交所有权」，而在 017 之前没有任何
  # API 能做这件事；演练最后一步就是把这条链跑通。
  python scripts/member_drill.py
}

step_media_scan_drill() {
  # 媒体扫描边界（发布清单「病毒扫描」那一半）：020 之后一格 clean 只能来自引擎判决，
  # 被拒的字节在落桶之前就被拒。这一步量的是数据库侧与服务侧的牙：伪造判决必须被拒、
  # 诚实记为 unscanned 的资产必须照常可听、伪造 token 必须仍然被挡。
  python scripts/media_scan_drill.py
}

step_report_drill() {
  # 对外公示的侵权通知收件口（021）：这是全平台唯一一条无需账号、无需会话、无需工作区就能写入的路径，
  # 所以这一步量的是三件事——它不能回答「这个 id 存不存在」（三种 subject 的响应逐字段相同）、
  # 举报人的邮箱不会顺着租户能看见的那张单子漏出去（assets.py 的 SELECT * 看得见案件 evidence）、
  # 以及通知是证据而不是工单：建成后任何人（含超级用户角色）改不动、删不掉，只能另加一条处置记录。
  # 还有一条是这一轮才补上的：表上的 CHECK 与请求模型的字面规则不完全同形（btrim 与 min_length），
  # 数据库拒的就是 422，不得以 500 的样子出现。
  python scripts/report_drill.py
}

step_commercial() {
  build_images_in_order -f docker-compose.yml -f docker-compose.commercial-test.yml
  docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml up -d
  docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml --profile commercial-test run --rm acceptance-commercial
}

step_reservation_race() {
  # Needs the commercial overlay still up: an exclusive offer requires approved rights.
  python scripts/reservation_race.py
}

step_provider_regression() {
  python scripts/provider_regression.py "${REGRESSION_JOBS:-100}" "${REGRESSION_CONCURRENCY:-8}"
}

step_reconcile_market() {
  python scripts/reconcile_market.py
}

step_hold_drill() {
  # Legal Hold 的真实覆盖面：哪些门因为挂押拒绝、用哪句话拒绝、哪些门根本不读这个标记，
  # 以及清单里哪一条谓词在现在的写入面下根本到不了（被前面的能力位/新鲜度判断挡在后面）。
  # 需要商业覆层还挂着：市场那一层的门只在 approved 权益下才打得开。
  python scripts/hold_drill.py
}

step_browser_a11y() {
  # The self-test arm proves the audit can fire before its verdict is trusted.
  python scripts/browser_a11y.py --self-test
  python scripts/browser_a11y.py
}

step_generic_rest_roundtrip() {
  # 用「第三方适配器」再跑一批生成：MUSIC_PROVIDER=generic_rest，端点仍指向本仓库自带的模拟器
  # （它已经提供该适配器默认的那组 /v1 路径）。第三个参数会让 provider_regression 先核对
  # /api/bootstrap 报出的 provider 身份，覆层没生效就直接判红，而不是静默沿用默认适配器跑绿。
  # 这不等于真实 Provider：合同、凭据与对方的错误词汇仍然缺，见 G9 条目。
  build_images_in_order -f docker-compose.yml -f docker-compose.generic-rest.yml
  docker compose -f docker-compose.yml -f docker-compose.generic-rest.yml up -d
  python scripts/provider_regression.py "${GENERIC_REST_JOBS:-25}" 4 generic_rest
}

step_capacity() {
  # 容量 Gate 会用 capacity500 覆层重起整套栈；先把商业/主栈停掉以释放宿主机端口。
  docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml down --remove-orphans >/dev/null 2>&1 || true
  docker compose down --remove-orphans >/dev/null 2>&1 || true
  ./scripts/capacity-gate-500.sh
}

# ---- 主流程 ----
run_step "static-verify" step_static_verify
run_step "compose-up" step_stack_up
run_step "acceptance" step_acceptance
run_step "contract-test" step_contract_test
run_step "chaos-worker-recovery" step_chaos
run_step "lease-contention" step_lease_contention
run_step "restore-fidelity-snapshot" step_fidelity_snapshot
run_step "backup-restore" step_backup_restore
run_step "restore-fidelity-compare" step_fidelity_compare
run_step "acceptance-rerun" step_acceptance_rerun
run_step "erasure-drill" step_erasure_drill
run_step "mfa-drill" step_mfa_drill
run_step "member-drill" step_member_drill
run_step "media-scan-drill" step_media_scan_drill
run_step "report-drill" step_report_drill
run_step "commercial-flow" step_commercial
run_step "reservation-race" step_reservation_race
run_step "market-reconciliation" step_reconcile_market
run_step "hold-drill" step_hold_drill
run_step "provider-regression-100" step_provider_regression

# 放在浏览器验收之后：这一步会用覆层重建 api/worker，把 provider 身份换成 generic_rest。
run_step "generic-rest-roundtrip" step_generic_rest_roundtrip

if [ "${BROWSER:-0}" = "1" ]; then
  run_step "browser-a11y" step_browser_a11y
else
  step "browser-a11y (skipped)"
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '%s | %s | %s | %s\n' "browser-a11y" "SKIPPED (BROWSER=1 才执行)" "$now" "$now" >> "$RESULTS_FILE"
  echo "提示：真实浏览器验收默认跳过；如需执行请设置 BROWSER=1（需要 playwright + Chromium，见 scripts/requirements-browser.txt）。"
fi

if [ "${CAPACITY:-0}" = "1" ]; then
  run_step "capacity-gate-500" step_capacity
else
  step "capacity-gate-500 (skipped)"
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '%s | %s | %s | %s\n' "capacity-gate-500" "SKIPPED (CAPACITY=1 才执行)" "$now" "$now" >> "$RESULTS_FILE"
  echo "提示：容量 Gate 默认跳过；如需执行请设置 CAPACITY=1（耗时较长且依赖 Provider）。"
fi

finish_evidence

echo ""
echo "================================================================================"
echo "最终结论：全部验收步骤通过 ✅"
echo "证据目录: $EVIDENCE_DIR"
echo "================================================================================"
