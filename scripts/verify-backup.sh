#!/usr/bin/env sh
set -eu
DIR="${1:?usage: scripts/verify-backup.sh backups/<timestamp>}"
cd "$DIR"
sha256sum -c SHA256SUMS
[ -s postgres.dump ]
[ -f backup-manifest.json ]
echo "Backup verification passed"
