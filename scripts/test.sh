#!/usr/bin/env sh
set -eu
docker compose --profile test build acceptance
docker compose --profile test run --rm acceptance
