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

./scripts/static-verify.sh
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
if [ "${CAPACITY_FROM_HOST:-0}" = "1" ]; then
  published=$(docker compose $FILES port gateway 80 | cut -d: -f2 | tail -1)
  [ -n "$published" ] || { echo "::error title=gateway-port::compose 没有报告 gateway:80 的发布端口"; exit 1; }
  python scripts/gateway_identity.py --base-url "http://127.0.0.1:$published" --port "$published" || exit 1
  python scripts/load-test-500.py \
    --base-url "http://127.0.0.1:$published" \
    --path /api/projects \
    --email owner@example.local \
    --password demo-owner \
    --users "${CAPACITY_USERS:-500}" \
    --requests-per-user "${CAPACITY_REQUESTS_PER_USER:-2}" \
    --max-error-rate "${CAPACITY_MAX_ERROR_RATE:-1}" \
    --max-p95-ms "${CAPACITY_MAX_P95_MS:-800}" | tee "$log"
else
docker compose $FILES --profile capacity run --rm loadtest \
  --base-url http://gateway \
  --path /api/projects \
  --email owner@example.local \
  --password demo-owner \
  --users "${CAPACITY_USERS:-500}" \
  --requests-per-user "${CAPACITY_REQUESTS_PER_USER:-2}" \
  --max-error-rate "${CAPACITY_MAX_ERROR_RATE:-1}" \
  --max-p95-ms "${CAPACITY_MAX_P95_MS:-800}" | tee "$log"
fi

# Domain correctness still matters after the traffic burst.
docker compose $FILES --profile test run --rm acceptance | tee -a "$log"

echo "capacity evidence: $log"
