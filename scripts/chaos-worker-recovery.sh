#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."

# This step measures ONE claimant: it kills `worker` and waits for its restarted self to take the
# lease back. The contention profile's `worker-b` polls the same queue from the same image, so if it
# is up the two readings separate -- measured 2026-09-27T03:53:01Z (step 5 of
# release-evidence/acceptance-20260927T035001Z): a worker-b started by hand 18 minutes earlier held
# the lease at the moment of the failure (`generation_jobs.lease_owner = worker-0a906393`, its
# startup identity, still heartbeating), while the assertion that timed out was about the restarted
# `worker` (identity worker-e02a9f18). The message said "expired lease was not reclaimed by the
# restarted worker" for a lease that had been reclaimed by a different worker.
# Remove, never just stop, for the same reason lease-contention.sh gives: a stopped container keeps
# a stale network id and the next `up` dies with "network <id> not found".
docker compose --profile contention rm -fs worker-b >/dev/null 2>&1 || true
claimants=$(docker compose ps --services --filter status=running | grep -c '^worker' || true)
if [ "$claimants" != "1" ]; then
  echo "CHAOS PREFLIGHT FAIL: this step needs exactly one running worker claimant, found $claimants:"
  docker compose ps --services --filter status=running | grep '^worker' || true
  exit 1
fi
echo "chaos preflight: exactly one claimant is polling the queue"

python scripts/chaos_worker_recovery.py submit
docker compose kill -s KILL worker
sleep "${LEASE_WAIT_SECONDS:-35}"
docker compose up -d worker
python scripts/chaos_worker_recovery.py verify
