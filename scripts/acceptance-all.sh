#!/usr/bin/env bash
#
# acceptance-all.sh — 一键 E2E 验收管线（Resonance AI 音乐资产平台）
#
# 在部署主机（具备 Docker + Compose v2）上运行，按固定顺序串联现有的验证脚本：
#   static-verify → compose 起栈 → acceptance(test.sh) → contract-test →
#   chaos-worker-recovery → backup → restore(带确认) → 复跑 acceptance →
#   commercial-flow(commercial-test 覆层) → capacity-gate-500(可选, CAPACITY=1)
#
# 每步打印醒目的分节与时间戳；任一步失败即打印诊断并 exit 1。
# 全部通过后，将步骤摘要与 `docker compose logs --no-color` 归档到
#   release-evidence/acceptance-<时间戳>/
#
# 注意：本脚本的 shebang 为 bash，但为兼容 CI 里的 `sh -n` 静态校验，
# 全程保持 POSIX sh 语法（不依赖数组、[[ ]]、local、process substitution）。
set -euo pipefail

cd "$(dirname "$0")/.."

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_DIR="release-evidence/acceptance-${STAMP}"
RESULTS_FILE="${EVIDENCE_DIR}/SUMMARY.txt"
COMPOSE_LOG="${EVIDENCE_DIR}/compose-logs.txt"
COMMERCIAL_LOG="${EVIDENCE_DIR}/commercial-compose-logs.txt"

mkdir -p "$EVIDENCE_DIR"

# 步骤序号，供 step()/run_step() 使用，并在 SUMMARY 中作为证据来源。
step_num=0

# 初始化 SUMMARY 头。
{
  echo "Resonance AI Music Asset Platform — E2E Acceptance"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "evidence_dir=$EVIDENCE_DIR"
  echo "host=$(hostname 2>/dev/null || echo unknown)"
  echo "git_commit=$(git rev-parse HEAD 2>/dev/null || echo unavailable)"
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
step_static_verify() {
  ./scripts/static-verify.sh
}

step_stack_up() {
  docker compose up --build -d
  docker compose ps
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

step_commercial() {
  docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml up --build -d
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
run_step "commercial-flow" step_commercial
run_step "reservation-race" step_reservation_race
run_step "market-reconciliation" step_reconcile_market
run_step "provider-regression-100" step_provider_regression

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
