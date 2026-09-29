#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
FILES="-f docker-compose.yml -f docker-compose.capacity500.yml"
mkdir -p capacity-results
stamp=$(date -u +%Y%m%dT%H%M%SZ)
log="capacity-results/$stamp.log"
cleanup() {
  if [ "${KEEP_CAPACITY_STACK:-0}" != "1" ]; then
    docker compose $FILES down -v >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

# An inner verifier must not speak the metric protocol under this step's name: `run_step` copies every line
# starting with `metric ` into SUMMARY keyed by the *outer* step, which made acceptance-20260929T032931Z
# publish `metrics capacity-gate-500 unit_passed=483` -- static-verify's reading, attributed to the capacity leg.
./scripts/static-verify.sh | sed 's/^metric /inner-step (static-verify, not this step) metric /'
cp -n .env.example .env 2>/dev/null || true

# One image at a time, then start them. The fan-out is the hazard, not the build: on this 4 vCPU VM,
# `up --build` running every image at once is what killed worker's `apt-get install ffmpeg` with SIGKILL
# (rc 137) while two other images were pulling wheels -- measured in acceptance-20260926T161826Z and
# fixed in `step_stack_up` (scripts/acceptance-all.sh:152-172), which this mirrors including the
# profile-blindness lesson: enumerate what `compose config` reports with `--profile '*'`, and build only
# the services that actually declare a `build:` section.
targets="postgres redis minio migrate provider-emulator payment-emulator api worker web admin gateway"
for service in $(docker compose $FILES --profile '*' config --format json | python -c '
import json, sys
wanted = set("postgres redis minio migrate provider-emulator payment-emulator api worker web admin gateway".split())
services = json.load(sys.stdin)["services"]
print(" ".join(sorted(n for n, s in services.items() if n in wanted and s.get("build"))))'); do
  docker compose $FILES build "$service" || exit 1
done
docker compose $FILES up -d $targets

# The client's own location is part of the measurement: in here it shares the 4 vCPU VM with the thing it
# is timing, which the cost doc names as the reason the tail cannot be signed. CAPACITY_FROM_HOST=1 moves
# the client to this machine and pays for it with an identity gate -- a published port is what compose
# says it is, not proof the traffic reached it (measured 2026-09-28: 8080 answered for another project's
# container, and the gateway's own published port was also claimed by an ssh listener while the leg ran).
traffic_rc=0
if [ "${CAPACITY_FROM_HOST:-0}" = "1" ]; then
  # `set -e` would abort on the assignment itself, beating the message below -- so capture the failure
  # here and let the empty-value branch say what happened.
  published=$(docker compose $FILES port gateway 80 2>/dev/null | cut -d: -f2 | tail -1 || true)
  [ -n "$published" ] || { echo "::error title=gateway-port::compose 没有报告 gateway:80 的发布端口" \
    "（端口未发布，或输出形状不是 host:port）"; exit 1; }
  { echo "identity gate: base-url=http://127.0.0.1:$published port=$published"; \
    python scripts/gateway_identity.py --base-url "http://127.0.0.1:$published" --port "$published" \
      --compose-files "docker-compose.yml,docker-compose.capacity500.yml"; } \
    | tee -a "$log"
  [ "${PIPESTATUS[0]}" = "0" ] || exit 1
  python scripts/load-test-500.py \
    --base-url "http://127.0.0.1:$published" \
    --path /api/projects \
    --email owner@example.local \
    --password demo-owner \
    --users "${CAPACITY_USERS:-500}" \
    --requests-per-user "${CAPACITY_REQUESTS_PER_USER:-2}" \
    --max-error-rate "${CAPACITY_MAX_ERROR_RATE:-1}" \
    --max-p95-ms "${CAPACITY_MAX_P95_MS:-800}" | tee "$log" || traffic_rc=$?
else
docker compose $FILES --profile capacity run --rm loadtest \
  --base-url http://gateway \
  --path /api/projects \
  --email owner@example.local \
  --password demo-owner \
  --users "${CAPACITY_USERS:-500}" \
  --requests-per-user "${CAPACITY_REQUESTS_PER_USER:-2}" \
  --max-error-rate "${CAPACITY_MAX_ERROR_RATE:-1}" \
  --max-p95-ms "${CAPACITY_MAX_P95_MS:-800}" | tee "$log" || traffic_rc=$?
fi

# Domain correctness still matters after the traffic burst, and the row-23 criterion in
# docs/E2E_ACCEPTANCE_RUNBOOK.md states both halves together ("错误率 ≤ 1% 且 p95 ≤ 800ms 且随后 acceptance 全过"),
# so a red traffic leg still owes the regression. It used to be unreachable: `set -euo pipefail` aborted at the
# `| tee "$log"` line above, and the step log of acceptance-20260929T032931Z row 23 never prints the evidence
# marker below -- the gate could only ever report one of its two halves.
accept_rc=0
docker compose $FILES --profile test run --rm acceptance | tee -a "$log" || accept_rc=$?

# This step's own readings, in the protocol `run_step` copies into SUMMARY.txt keyed by the step name. Read
# from the log the tool just wrote; if a reading is missing the line is refused rather than filled with 0,
# because a zero would be stamped as a measurement. `|| true` on each extraction: an empty grep under pipefail
# would abort the assignment (the same trap that silenced step 2 in 20260928T184002Z).
p50=$(grep -oE 'p50=[0-9.]+' "$log" | tail -1 | cut -d= -f2 || true)
p95=$(grep -oE 'p95=[0-9.]+' "$log" | tail -1 | cut -d= -f2 || true)
p99=$(grep -oE 'p99=[0-9.]+' "$log" | tail -1 | cut -d= -f2 || true)
rps=$(grep -oE 'throughput=[0-9.]+' "$log" | tail -1 | cut -d= -f2 || true)
# `(^|[^_])` is load-bearing: the bare pattern also matches the threshold on the verdict line
# (`max_error_rate=1.0%`), which would publish a limit as a measurement. Measured on the first arm after this
# change, which read `error_rate_pct=1.0` while the tool had printed `error_rate=0.000%`.
err=$(grep -oE '[^_]error_rate=[0-9.]+' "$log" | tail -1 | sed 's/^[^e]*error_rate=//' || true)
if [ -n "$p50" ] && [ -n "$p95" ]; then
  printf 'metric users=%s requests_per_user=%s p50_ms=%s p95_ms=%s p99_ms=%s throughput_rps=%s error_rate_pct=%s acceptance_rc=%s\n' \
    "${CAPACITY_USERS:-500}" "${CAPACITY_REQUESTS_PER_USER:-2}" "$p50" "$p95" "${p99:-unset}" \
    "${rps:-unset}" "${err:-unset}" "$accept_rc"
else
  echo "metric unavailable: the load tool printed no latency line (log $log)"
fi

echo "capacity evidence: $log"

# Both halves are in the verdict now: the traffic criterion first (rc 2 is the load tool's own "gate=FAIL"),
# then the domain regression, and a red traffic leg is reported even when the regression passes.
[ "$traffic_rc" = "0" ] || exit "$traffic_rc"
[ "$accept_rc" = "0" ] || exit "$accept_rc"
