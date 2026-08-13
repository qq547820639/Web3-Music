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
    data = yaml.safe_load((ROOT / "docker-compose.capacity500.yml").read_text())
    assert data["services"]["api"]["environment"]["API_WORKERS"] == "${API_WORKERS:-4}"
    assert data["services"]["worker"]["environment"]["WORKER_CONCURRENCY"] == "${WORKER_CONCURRENCY:-12}"
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
