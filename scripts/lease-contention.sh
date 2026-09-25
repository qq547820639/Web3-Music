#!/usr/bin/env sh
#
# lease-contention.sh — release gate G9-2, the half chaos-worker-recovery.sh does not cover.
#
# chaos-worker-recovery.sh proves a killed Worker's lease is later reclaimed. This proves
# the other direction: two Workers polling at the same time cannot claim the same job
# twice, cannot duplicate candidate rows, and cannot settle credits twice.
#
# Assumes the main stack is already up (api on :8000). Only worker-b is added and removed.
set -eu
cd "$(dirname "$0")/.."

JOBS="${CONTENTION_JOBS:-8}"
TIMEOUT="${CONTENTION_TIMEOUT:-300}"

cleanup() {
  # Remove, never just stop: a stopped worker-b keeps the NetworkID of whatever project
  # network existed at the time, and once `compose down` recreates that network the next
  # `up` tries to start the old container and fails with "network <id> not found".
  docker compose --profile contention rm -fs worker-b >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# Both claimants must be polling before the burst, or they never actually contend.
cleanup
docker compose --profile contention up -d worker worker-b
sleep "${WORKER_SETTLE_SECONDS:-12}"

python scripts/lease_contention.py prepare "$JOBS"
python scripts/lease_contention.py verify "$TIMEOUT"
