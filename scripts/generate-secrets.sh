#!/usr/bin/env sh
set -eu
out="${1:-.env.generated}"
[ -e "$out" ] && { echo "$out already exists" >&2; exit 1; }
secret(){ python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
}
cat > "$out" <<EOF
JWT_SECRET=$(secret)
MEDIA_SIGNING_SECRET=$(secret)
PROVIDER_WEBHOOK_SECRET=$(secret)
PAYMENT_WEBHOOK_SECRET=$(secret)
POSTGRES_ADMIN_PASSWORD=$(secret)
POSTGRES_APP_PASSWORD=$(secret)
POSTGRES_WORKER_PASSWORD=$(secret)
MINIO_ROOT_USER=resonance
MINIO_ROOT_PASSWORD=$(secret)
EOF
chmod 600 "$out"
echo "$out"
