#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
docker compose --profile test build acceptance
docker compose --profile test run --rm acceptance pytest -q test_provider_contract.py test_payment_contract.py
