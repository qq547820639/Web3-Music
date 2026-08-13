#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
DIR="${1:?usage: RESTORE_CONFIRM=YES scripts/restore.sh backups/<timestamp>}"
[ "${RESTORE_CONFIRM:-}" = "YES" ] || { echo "Set RESTORE_CONFIRM=YES to replace local data" >&2; exit 2; }
[ -f "$DIR/postgres.dump" ] && [ -d "$DIR/minio-data" ] || { echo "invalid backup directory" >&2; exit 2; }
./scripts/verify-backup.sh "$DIR"

echo "Stopping application writers"
docker compose stop api worker web admin prometheus >/dev/null 2>&1 || true
docker compose up -d postgres minio

echo "Restoring PostgreSQL"
docker compose exec -T postgres dropdb -U music_admin --if-exists music
docker compose exec -T postgres createdb -U music_admin music
cat "$DIR/postgres.dump" | docker compose exec -T postgres pg_restore -U music_admin -d music --no-owner --no-privileges

echo "Restoring local MinIO data"
docker compose stop minio
docker compose run --rm --no-deps --entrypoint sh minio -c 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} +' >/dev/null
docker compose cp "$DIR/minio-data/." minio:/data >/dev/null
docker compose start minio

echo "Starting platform"
docker compose up -d

echo "Restore complete. Run: docker compose --profile test run --rm acceptance"
