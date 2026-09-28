#!/usr/bin/env sh
set -eu
# Ask compose which host port the gateway actually publishes instead of assuming 8080: this machine has
# an unrelated service on 8080, and a smoke check that talks to it prints four greens for a dead stack.
if [ -z "${BASE_URL:-}" ]; then
  _gwport=$(docker compose port gateway 80 2>/dev/null | cut -d: -f2 || true)
  [ -n "$_gwport" ] || { echo "smoke: compose 没有报告 gateway:80 的发布端口，请显式给 BASE_URL"; exit 2; }
  base="http://127.0.0.1:${_gwport}"
else
  base="${BASE_URL}"
fi
for path in /gateway-health /health /ready /docs; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$base$path")
  case "$path:$code" in
    /gateway-health:200|/health:200|/ready:200|/docs:200) ;;
    *) echo "smoke failed $path -> $code" >&2; exit 1;;
  esac
done
echo "smoke passed: $base"
