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


def test_vendor_contract_dialect_is_accepted_end_to_end():
    """The body shape GenericRESTAdapter sends, checked against the server that must take it.

    This is the shape from shared/contracts/provider-submit-v1.schema.json. The emulator used to
    understand only its own flat dialect, so pointing MUSIC_PROVIDER=generic_rest at it failed
    every submit with 422 -- two adapters, two undocumented request shapes, and no test that
    covered the difference. The client side of the same contract is pinned in
    tests/unit/test_provider_contract_schema.py.
    """
    key = "contract-vendor-" + uuid.uuid4().hex
    body = {"external_request_id": key, "model": "voice-v9", "candidate_count": 2,
            "song_spec": {"title": "Vendor dialect", "lyrics": "line one\nline two", "styles": ["synth pop"], "bpm": 96}}
    created = requests.post(BASE + "/v1/jobs", headers={"Idempotency-Key": key}, json=body, timeout=20)
    assert created.status_code == 202, created.text
    job_id = created.json()["id"]

    end = time.time() + 35
    result = None
    while time.time() < end:
        result = requests.get(BASE + f"/v1/jobs/{job_id}", timeout=20).json()
        if result["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(.5)
    assert result and result["status"] == "completed", result
    assert len(result["results"]) == 2
    audio = [c["audio_url"] for c in result["results"] if c["status"] == "completed"]
    assert len(audio) == 2 and all(requests.get(a, timeout=20).status_code == 200 for a in audio)

    # a body that violates the contract is refused as unprocessable, not silently accepted
    bad = requests.post(BASE + "/v1/jobs", headers={"Idempotency-Key": key + "-bad"},
                        json={"candidate_count": 2, "song_spec": {"lyrics": "no title"}}, timeout=20)
    assert bad.status_code == 422, bad.text
