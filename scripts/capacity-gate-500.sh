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
# The suite's precondition is the seeded ledger: migration 001:515-524 grants every workspace exactly 1000
# credits, one full suite run spends 40 (measured: available 960 after a single run), and `POST /jobs` raises
# 402 with `available=…, required=…` when the balance is short of the quote. Reaching this leg after 22 chain
# rows means the drills have already spent the grant down, so the run measures "did the burst break domain
# correctness" only if the database is renewed first -- otherwise it measures how much budget the earlier rows
# left. down -v + up -d + health wait, then the same run.
docker compose $FILES --profile '*' down -v >/dev/null 2>&1 || true
docker compose $FILES up -d >>"$log" 2>&1
for probe in $(seq 1 60); do
  h=$(docker compose $FILES ps --format '{{.Health}}' 2>/dev/null | grep -c healthy)
  [ "${h:-0}" -ge 8 ] && break
  sleep 5
done
echo "regression database: renewed (down -v + up -d, ${h:-0} containers healthy after ${probe} probes)"
docker compose $FILES --profile test run --rm acceptance | tee -a "$log" || accept_rc=$?

# This step's own readings, rendered by scripts/capacity_metrics.py so the parsing carries a resident test
# (tests/unit/test_capacity_metrics.py, fixture: a real arm transcript). The renderer names what is missing
# instead of filling a zero, and never publishes a threshold as a measurement -- the inline version it replaces
# read `error_rate_pct=1.0` from `max_error_rate=1.0%` while the tool had printed `error_rate=0.000%`.
python scripts/capacity_metrics.py "$log" "$accept_rc" \
  || echo "metric unavailable: the renderer exited non-zero (log $log)"

echo "capacity evidence: $log"

# Both halves are in the verdict now: the traffic criterion first (rc 2 is the load tool's own "gate=FAIL"),
# then the domain regression, and a red traffic leg is reported even when the regression passes.
[ "$traffic_rc" = "0" ] || exit "$traffic_rc"
[ "$accept_rc" = "0" ] || exit "$accept_rc"
