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
