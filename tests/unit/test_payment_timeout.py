"""Regression tests for the payment-emulator timeout fallback (A9).

A `processing` / `requires_action` intent used to never resolve, leaving the
order side stuck in `payment_pending` forever. After the fix it must resolve to
`failed` after PAYMENT_TIMEOUT_SECONDS, while the immediate `success`/`failed`
scenarios keep their original behaviour.
"""

import importlib.util
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

_SRC = Path(__file__).parents[2] / "services" / "payment-emulator" / "main.py"


def load_module(tmp_path: Path, timeout: int):
    os.environ["SQLITE_PATH"] = str(tmp_path / "payment.db")
    os.environ["PAYMENT_TIMEOUT_SECONDS"] = str(timeout)
    spec = importlib.util.spec_from_file_location("payment_emulator_timeout_under_test", _SRC)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _create_intent(client, scenario: str, key: str):
    return client.post(
        "/v1/intents",
        json={
            "amount": 1000,
            "currency": "USD",
            "scenario": scenario,
            "callback_url": "http://127.0.0.1:9/ignored",
            "metadata": {},
        },
        headers={"Idempotency-Key": key},
    ).json()


def _wait_status(client, intent_id: str, expect: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = client.get(f"/v1/intents/{intent_id}").json()["status"]
        if status == expect:
            return status
        time.sleep(0.05)
    return status


def test_processing_intent_resolves_to_failed_after_timeout(tmp_path):
    module = load_module(tmp_path, timeout=0)
    client = TestClient(module.app)
    intent = _create_intent(client, "processing", "processing-1")
    assert intent["status"] == "processing"
    assert _wait_status(client, intent["id"], "failed") == "failed"


def test_requires_action_intent_resolves_to_failed_after_timeout(tmp_path):
    module = load_module(tmp_path, timeout=0)
    client = TestClient(module.app)
    intent = _create_intent(client, "requires_action", "requires-action-1")
    assert intent["status"] == "requires_action"
    assert _wait_status(client, intent["id"], "failed") == "failed"


def test_success_and_failed_paths_unchanged(tmp_path):
    module = load_module(tmp_path, timeout=60)
    client = TestClient(module.app)
    ok = _create_intent(client, "success", "success-1")
    bad = _create_intent(client, "failed", "failed-1")
    # success/failed resolve immediately and never wait for the timeout worker
    assert client.get(f"/v1/intents/{ok['id']}").json()["status"] == "succeeded"
    assert client.get(f"/v1/intents/{bad['id']}").json()["status"] == "failed"
