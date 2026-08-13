#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
STAMP="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
DEST="backups/$STAMP"
mkdir -p "$DEST"

writers_stopped=0
cleanup() {
  if [ "$writers_stopped" = "1" ]; then
    docker compose up -d api worker >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

echo "[0/4] Quiescing application writers for a cross-store local snapshot"
docker compose stop api worker >/dev/null 2>&1 || true
writers_stopped=1

echo "[1/4] PostgreSQL consistent dump"
docker compose exec -T postgres pg_dump -U music_admin -d music --format=custom --no-owner > "$DEST/postgres.dump"

echo "[2/4] Private object storage snapshot"
# Copy the contents of MinIO's local persistent data directory. This is suitable for
# the reference Compose stack. Production deployments must use managed DB snapshots,
# bucket versioning/replication and a coordinated recovery point.
mkdir -p "$DEST/minio-data"
docker compose cp minio:/data/. "$DEST/minio-data" >/dev/null

echo "[3/4] Manifest"
cat > "$DEST/backup-manifest.json" <<EOF
{"format_version":1,"created_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","database":"music","object_store":"minio-quiesced-local-snapshot","redis":"not_required-derived-data","release":"v13.0.0"}
EOF
(
  cd "$DEST"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

echo "[4/4] Resuming application writers"
docker compose up -d api worker >/dev/null
writers_stopped=0
trap - EXIT INT TERM

echo "Backup created: $DEST"
