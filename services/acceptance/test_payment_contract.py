"""Payment provider contract used by the commercial-flow reference implementation."""
import os
import uuid

import requests

BASE = os.getenv("PAYMENT_BASE_URL", "http://payment-emulator:8020")


def test_payment_and_refund_idempotency_contract():
    key = "payment-contract-" + uuid.uuid4().hex
    callback = "http://api:8000/api/payment-webhooks/emulator"
    metadata = {"payment_id": str(uuid.uuid4()), "order_id": str(uuid.uuid4()), "workspace_id": "11111111-1111-1111-1111-111111111111"}
    body = {"amount": 1200, "currency": "USD", "scenario": "processing", "callback_url": callback, "metadata": metadata}
    first = requests.post(BASE + "/v1/intents", headers={"Idempotency-Key": key}, json=body, timeout=20)
    second = requests.post(BASE + "/v1/intents", headers={"Idempotency-Key": key}, json=body, timeout=20)
    assert first.status_code == 200 and first.json()["id"] == second.json()["id"]
    conflict = requests.post(BASE + "/v1/intents", headers={"Idempotency-Key": key}, json={**body, "amount": 1300}, timeout=20)
    assert conflict.status_code == 409


def test_successful_intent_is_refundable_once_per_idempotency_key():
    key = "payment-success-" + uuid.uuid4().hex
    callback = "http://api:8000/api/payment-webhooks/emulator"
    metadata = {"payment_id": str(uuid.uuid4()), "order_id": str(uuid.uuid4()), "workspace_id": "11111111-1111-1111-1111-111111111111"}
    body = {"amount": 1000, "currency": "USD", "scenario": "success", "callback_url": callback, "metadata": metadata}
    intent = requests.post(BASE + "/v1/intents", headers={"Idempotency-Key": key}, json=body, timeout=20)
    assert intent.status_code == 200
    refund_key = "refund-contract-" + uuid.uuid4().hex
    refund_body = {"intent_id": intent.json()["id"], "amount": 400, "callback_url": callback, "metadata": {**metadata, "refund_id": str(uuid.uuid4())}}
    first = requests.post(BASE + "/v1/refunds", headers={"Idempotency-Key": refund_key}, json=refund_body, timeout=20)
    second = requests.post(BASE + "/v1/refunds", headers={"Idempotency-Key": refund_key}, json=refund_body, timeout=20)
    assert first.status_code == 200 and first.json()["id"] == second.json()["id"]
    over = requests.post(BASE + "/v1/refunds", headers={"Idempotency-Key": "over-" + uuid.uuid4().hex}, json={**refund_body, "amount": 700}, timeout=20)
    assert over.status_code == 409
