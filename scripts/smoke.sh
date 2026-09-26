#!/usr/bin/env sh
set -eu
base="${BASE_URL:-http://127.0.0.1:8080}"
for path in /gateway-health /health /ready /docs; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$base$path")
  case "$path:$code" in
    /gateway-health:200|/health:200|/ready:200|/docs:200) ;;
    *) echo "smoke failed $path -> $code" >&2; exit 1;;
  esac
done
echo "smoke passed: $base"
