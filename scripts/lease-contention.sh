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

# And both claimants must be the same build, namely the one under certification. This is not
# tidiness: in acceptance-20260927T030055Z step 6, worker-b was still running an image from hours
# earlier (the chain only rebuilt what `compose config --services` lists, and a service behind a
# profile is not in it), so the race was between a new worker and an old one -- the old code wrote
# the literal 'clean' into media_assets, migration 020's trigger refused it, a job settled partial
# and the step failed with a message about leases. A reading from two different builds is not
# evidence about concurrency at all, so it is refused before any job is submitted.
tree_sha=$(sha256sum services/worker/worker.py | cut -c1-16)
for claimant in worker worker-b; do
  inside=$(docker compose exec -T "$claimant" sha256sum /app/worker.py | cut -c1-16)
  if [ "$inside" != "$tree_sha" ]; then
    echo "LEASE CONTENTION FAIL: $claimant runs /app/worker.py $inside, the tree has $tree_sha"
    exit 1
  fi
  echo "contention preflight: $claimant's /app/worker.py == the tree ($tree_sha)"
done

python scripts/lease_contention.py prepare "$JOBS"
python scripts/lease_contention.py verify "$TIMEOUT"
