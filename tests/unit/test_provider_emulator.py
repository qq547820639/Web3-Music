import importlib.util
import os
import struct
import time
import wave
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
    # Polled, not slept-and-prayed: clips are generated off the request path, so the moment a job
    # turns terminal depends on how fast this machine is. An earlier version slept 2.1s and read
    # whatever came back, which pinned a timing coincidence rather than the contract.
    result = wait_for_terminal(client, created["id"])
    assert result["status"] == "partial"
    assert sum(x["status"] == "completed" for x in result["results"]) == 1
    url = next(x["audio_url"] for x in result["results"] if x["status"] == "completed")
    media = client.get(url.replace("http://testserver", ""))
    assert media.status_code == 200
    assert media.content[:4] == b"RIFF"


def wait_for_terminal(client, job_id, timeout=40.0):
    end = time.time() + timeout
    seen = []
    while time.time() < end:
        body = client.get(f"/v1/jobs/{job_id}").json()
        seen.append(body["status"])
        if body["status"] in {"completed", "partial", "failed", "cancelled"}:
            return body
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} never went terminal, saw {seen}")


def fake_writer(delay: float):
    """A write_wav that costs a known amount instead of a machine-dependent amount."""
    def write(path, seed, bpm, ordinal):
        time.sleep(delay)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
            handle.writeframes(struct.pack("<" + "h" * 16, *([0] * 16)))
    return write


def test_no_client_waits_for_clip_synthesis(tmp_path):
    """Re-submitting an aged job must cost the same as submitting a fresh one.

    The regression this pins: `create_job` is an async def, and the idempotency-replay branch
    generated every missing clip inline. Measured on the running stack, replaying an eight-candidate
    job cost 9.98s and an unrelated GET /health queued behind it cost 9.95s -- one client's poll
    stopped the whole emulator. The acceptance suite's 20s read timeout blew on exactly that request.
    """
    module = load_module(tmp_path)
    module.write_wav = fake_writer(0.5)
    client = TestClient(module.app)
    payload = {"title": "Replay", "lyrics": "line", "styles": "piano", "bpm": 72,
               "candidate_count": 8, "scenario": "success"}
    key = "replay-latency"
    fresh = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": key})
    assert fresh.status_code == 202
    job_id = fresh.json()["id"]
    time.sleep(2.2)  # past the emulator's own 'processing' window, so a replay used to generate here
    started = time.perf_counter()
    replay = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": key})
    elapsed = time.perf_counter() - started
    assert replay.status_code == 202
    assert replay.json()["id"] == job_id, "the replay must be the same job, not a second one"
    assert elapsed < 1.0, (
        f"an idempotent replay waited {elapsed:.2f}s on 8 clips at 0.5s each; "
        "clip synthesis has moved back onto the request path"
    )
    result = wait_for_terminal(client, job_id)
    assert sum(x["status"] == "completed" for x in result["results"]) == 8
    for clip in result["results"]:
        assert client.get(clip["audio_url"].replace("http://testserver", "")).status_code == 200, (
            "a published result pointed at a file that does not exist"
        )


def test_generation_failure_surfaces_instead_of_hanging(tmp_path):
    module = load_module(tmp_path)
    def broken(path, seed, bpm, ordinal):
        raise RuntimeError("synth blew up")
    module.write_wav = broken
    client = TestClient(module.app)
    payload = {"title": "Broken", "lyrics": "line", "styles": "piano", "bpm": 72,
               "candidate_count": 2, "scenario": "success"}
    job_id = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "broken"}).json()["id"]
    result = wait_for_terminal(client, job_id, timeout=20.0)
    assert result["status"] == "partial"
    assert all(x["error"]["code"] == "emulator_generation_error" for x in result["results"])


def test_a_half_written_clip_is_never_left_where_a_complete_one_is_read(tmp_path):
    """`if not path.exists()` treats a present file as finished, so publishing must be atomic."""
    module = load_module(tmp_path)
    client = TestClient(module.app)
    payload = {"title": "Atomic", "lyrics": "line", "styles": "piano", "bpm": 72,
               "candidate_count": 1, "scenario": "success"}
    job_id = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "atomic"}).json()["id"]
    result = wait_for_terminal(client, job_id)
    assert module.MEDIA_ROOT.is_dir()
    leftovers = [p.name for p in module.MEDIA_ROOT.iterdir() if ".part" in p.name]
    assert not leftovers, f"temporary clip files survived: {leftovers}"
    media = next(x["audio_url"] for x in result["results"] if x["status"] == "completed")
    assert client.get(media.replace("http://testserver", "")).status_code == 200
