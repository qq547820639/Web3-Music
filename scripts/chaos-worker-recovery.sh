#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
python scripts/chaos_worker_recovery.py submit
docker compose kill -s KILL worker
sleep "${LEASE_WAIT_SECONDS:-35}"
docker compose up -d worker
python scripts/chaos_worker_recovery.py verify
