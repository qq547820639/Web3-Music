import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient


def load_module(tmp_path: Path):
    os.environ["SQLITE_PATH"] = str(tmp_path / "payment.db")
    spec = importlib.util.spec_from_file_location(
        "payment_emulator_under_test",
        Path(__file__).parents[2] / "services" / "payment-emulator" / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_intent_idempotency_and_conflict(tmp_path):
    module = load_module(tmp_path)
    client = TestClient(module.app)
    payload = {
        "amount": 1200,
        "currency": "USD",
        "scenario": "processing",
        "callback_url": "http://127.0.0.1:9/ignored",
        "metadata": {"order_id": "order-1"},
    }
    first = client.post("/v1/intents", json=payload, headers={"Idempotency-Key": "intent-1"})
    retry = client.post("/v1/intents", json=payload, headers={"Idempotency-Key": "intent-1"})
    conflict = client.post("/v1/intents", json={**payload, "amount": 1300}, headers={"Idempotency-Key": "intent-1"})
    assert first.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409


def test_refund_never_exceeds_payment(tmp_path):
    module = load_module(tmp_path)
    client = TestClient(module.app)
    intent = client.post(
        "/v1/intents",
        json={"amount": 1000, "currency": "USD", "scenario": "success", "callback_url": "http://127.0.0.1:9/ignored", "metadata": {}},
        headers={"Idempotency-Key": "paid"},
    ).json()
    base = {"intent_id": intent["id"], "callback_url": "http://127.0.0.1:9/ignored", "metadata": {}}
    assert client.post("/v1/refunds", json={**base, "amount": 600}, headers={"Idempotency-Key": "r1"}).status_code == 200
    over = client.post("/v1/refunds", json={**base, "amount": 500}, headers={"Idempotency-Key": "r2"})
    assert over.status_code == 409
