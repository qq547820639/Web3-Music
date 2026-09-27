import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_gate_percentile_math():
    mod = load_module("capacity_loadtest", ROOT / "scripts/load-test-500.py")
    assert mod.percentile([1, 2, 3, 4, 5], 0.50) == 3
    assert mod.percentile([1, 2, 3, 4, 5], 0.95) == 5


def test_capacity_compose_has_low_cost_parallelism_and_loadtest():
    """The profile's numbers have to be the numbers a container receives.

    This test used to assert `API_WORKERS == "${API_WORKERS:-4}"` -- the interpolation's text, never its
    resolution. That is how the profile's own value went unused on every host: `.env.example` ships
    `API_WORKERS=1`, Compose resolved the name from `.env` (which CI and `capacity-gate-500.sh` both
    copy into place), and the "500-user capacity profile" ran one Uvicorn worker while the test stayed
    green. Measured on this stack with only the worker count changed, each arm recreated and repeated,
    p50 of GET /api/projects through the gateway at 500 users x 5 requests: 6501.0ms on one worker
    against 2106.3ms on four (and 1089.9/1091.5ms against 577.3/723.2ms at 128 users). The throughput
    and p95 readings did not separate across passes, so this test asserts the configuration, not a
    latency budget.
    """
    data = yaml.safe_load((ROOT / "docker-compose.capacity500.yml").read_text())
    api = data["services"]["api"]["environment"]
    worker = data["services"]["worker"]["environment"]
    assert api["API_WORKERS"] == "4"
    assert worker["WORKER_CONCURRENCY"] == "12"
    assert not any(isinstance(value, str) and "${" in value for value in api.values()), \
        "a dollar-brace value here is resolved from the host's .env, not from this file"
    # ...and the host default really does differ, which is what makes the assertion above load-bearing.
    example = (ROOT / ".env.example").read_text()
    assert "API_WORKERS=1" in example, "the host default is no longer the shadow this guard exists for"
    assert "loadtest" in data["services"]


def test_capacity_migration_indexes_hot_paths():
    sql = (ROOT / "db/migrations/006_capacity_and_cost_hardening.sql").read_text()
    for name in (
        "ix_jobs_ready_to_claim",
        "ix_jobs_expired_lease",
        "ix_provider_inbox_unprocessed",
        "ix_jobs_workspace_created",
    ):
        assert name in sql


def test_worker_long_poll_and_deterministic_media_key_are_present():
    text = (ROOT / "services/worker/worker.py").read_text()
    assert 'POLL_WINDOW_SECONDS","600"' in text
    assert "PROVIDER_POLL_MAX_INTERVAL_SECONDS" in text
    assert "/audio/{sha[:2]}/{sha}" in text
    assert "media=await store_media" in text


def test_gateway_allows_capacity_burst_behind_shared_nat():
    text = (ROOT / "services/gateway/nginx.conf").read_text()
    assert "rate=200r/s" in text
    assert "burst=600" in text
