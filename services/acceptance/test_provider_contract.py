"""Provider Adapter contract, runnable against the bundled emulator or an approved adapter test endpoint."""
import os
import time
import uuid

import requests

BASE = os.getenv("PROVIDER_BASE_URL", "http://provider-emulator:8010")


def test_capabilities_and_idempotent_generation_contract():
    capabilities = requests.get(BASE + "/v1/capabilities", timeout=20)
    assert capabilities.status_code == 200, capabilities.text
    body = capabilities.json()
    for key in ("async", "candidate_count_max", "supports_custom_lyrics", "supports_styles", "supports_cancel"):
        assert key in body

    key = "contract-" + uuid.uuid4().hex
    payload = {"title": "Provider contract", "lyrics": "line one\nline two", "styles": "piano pop", "bpm": 80, "candidate_count": 2, "scenario": "partial_success"}
    first = requests.post(BASE + "/v1/jobs", headers={"Idempotency-Key": key}, json=payload, timeout=20)
    second = requests.post(BASE + "/v1/jobs", headers={"Idempotency-Key": key}, json=payload, timeout=20)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    conflict = requests.post(BASE + "/v1/jobs", headers={"Idempotency-Key": key}, json={**payload, "title": "different"}, timeout=20)
    assert conflict.status_code == 409

    job_id = first.json()["id"]
    end = time.time() + 35
    result = None
    while time.time() < end:
        current = requests.get(BASE + f"/v1/jobs/{job_id}", timeout=20)
        assert current.status_code == 200
        result = current.json()
        if result["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(.5)
    assert result and result["status"] == "partial"
    assert len(result["results"]) == 2
    assert sum(1 for c in result["results"] if c["status"] == "completed") == 1
    audio = next(c["audio_url"] for c in result["results"] if c["status"] == "completed")
    media = requests.get(audio, timeout=20)
    assert media.status_code == 200 and media.headers["content-type"].startswith("audio/") and len(media.content) > 1024


def test_cancel_contract():
    key = "contract-cancel-" + uuid.uuid4().hex
    payload = {"title": "Cancel", "lyrics": "line", "styles": "piano", "bpm": 70, "candidate_count": 1, "scenario": "timeout"}
    created = requests.post(BASE + "/v1/jobs", headers={"Idempotency-Key": key}, json=payload, timeout=20)
    assert created.status_code == 202
    cancelled = requests.post(BASE + f"/v1/jobs/{created.json()['id']}/cancel", timeout=20)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
