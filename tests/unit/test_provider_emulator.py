import importlib.util
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient


def load_module(tmp_path: Path):
    os.environ["SQLITE_PATH"] = str(tmp_path / "provider.db")
    os.environ["MEDIA_ROOT"] = str(tmp_path / "media")
    spec = importlib.util.spec_from_file_location(
        "provider_emulator_under_test",
        Path(__file__).parents[2] / "services" / "provider-emulator" / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_provider_capability_and_idempotency_contract(tmp_path):
    module = load_module(tmp_path)
    client = TestClient(module.app)
    capabilities = client.get("/v1/capabilities").json()
    assert capabilities["async"] is True
    assert capabilities["commercial_rights"] == "blocked"
    payload = {"title": "Contract", "lyrics": "line", "styles": "piano", "bpm": 72, "candidate_count": 1, "scenario": "timeout"}
    first = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "job-1"})
    retry = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "job-1"})
    conflict = client.post("/v1/jobs", json={**payload, "title": "different"}, headers={"Idempotency-Key": "job-1"})
    assert first.status_code == 202
    assert retry.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409


def test_partial_success_creates_one_decodable_candidate(tmp_path):
    module = load_module(tmp_path)
    client = TestClient(module.app)
    payload = {"title": "Partial", "lyrics": "line", "styles": "piano", "bpm": 72, "candidate_count": 2, "scenario": "partial_success"}
    created = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "job-partial"}).json()
    time.sleep(2.1)
    result = client.get(f"/v1/jobs/{created['id']}").json()
    assert result["status"] == "partial"
    assert sum(x["status"] == "completed" for x in result["results"]) == 1
    url = next(x["audio_url"] for x in result["results"] if x["status"] == "completed")
    media = client.get(url.replace("http://testserver", ""))
    assert media.status_code == 200
    assert media.content[:4] == b"RIFF"
