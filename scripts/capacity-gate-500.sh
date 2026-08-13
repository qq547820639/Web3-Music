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

docker compose $FILES up --build -d postgres redis minio migrate provider-emulator payment-emulator api worker web admin gateway

docker compose $FILES --profile capacity run --rm loadtest \
  --base-url http://gateway \
  --path /api/projects \
  --email owner@example.local \
  --password demo-owner \
  --users "${CAPACITY_USERS:-500}" \
  --requests-per-user "${CAPACITY_REQUESTS_PER_USER:-2}" \
  --max-error-rate "${CAPACITY_MAX_ERROR_RATE:-1}" \
  --max-p95-ms "${CAPACITY_MAX_P95_MS:-800}" | tee "$log"

# Domain correctness still matters after the traffic burst.
docker compose $FILES --profile test run --rm acceptance | tee -a "$log"

echo "capacity evidence: $log"
