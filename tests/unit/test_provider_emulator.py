import importlib.util
import os
import struct
import threading
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


def synthesis_on_the_serving_thread(writes, services) -> bool:
    """Whether any clip was written by a thread that also answered a request.

    The claim the emulator makes in its own comment is positional, not temporal: `build_results` is
    "only ever called off the request path". A span cannot test that -- the pool's own work delays a
    correct replay by up to a second on this fixture (see the case below for the measured band) -- but
    thread identity states it exactly: if the bytes were written by a thread that served a request,
    some client waited for synthesis.
    """
    return bool(set(writes) & set(services))


def test_the_serving_thread_rule_fires_on_both_polarities():
    """The rule above must be able to catch the defect, not just to be quiet when nothing is wrong."""
    pool, request = 111, 222
    # Clips generated on the pool while a different thread answered: compliant.
    assert synthesis_on_the_serving_thread([pool, pool, pool], [request]) is False
    # No writes at all (the replay was answered from the stored row): compliant, and not the shape
    # the earlier span assertion could tell apart from a job that simply finished fast.
    assert synthesis_on_the_serving_thread([], [request]) is False
    # One clip written by the thread that served a request: the defect, and it fires whatever the clock
    # says -- at zero milliseconds, on a machine doing nothing else.
    assert synthesis_on_the_serving_thread([request], [request]) is True
    assert synthesis_on_the_serving_thread([pool, request], [request]) is True
    # A serving thread is only evidence when there was one; an empty service list means the spy never
    # ran, which the calling case refuses rather than passes.
    assert synthesis_on_the_serving_thread([pool], []) is False


def test_no_client_waits_for_clip_synthesis(tmp_path):
    """Re-submitting an aged job must do no synthesis work itself and must not wait for any.

    The regression this pins: `create_job` is an async def, and the idempotency-replay branch
    generated every missing clip inline. Measured on the running stack, replaying an eight-candidate
    job cost 9.98s and an unrelated GET /health queued behind it cost 9.95s -- one client's poll
    stopped the whole emulator. The acceptance suite's 20s read timeout blew on exactly that request.

    What used to catch it here was `elapsed < 1.0` -- a span, which this fixture itself makes noisy:
    the eight clips cost 0.5s each and run on the generator pool while the replay is being answered,
    and the GIL makes the correct replay wait for the thread that is holding it. Measured with
    `.scratch/probe_replay_margin.py` against this same module (working file, not in the tracked
    archive): ten repeats on an idle machine read 0.211s to 1.047s, one of them already past the
    bound with nothing wrong (same job id, no pool submit, `created_at` unmoved); ten repeats under
    eight spinning processes read 0.003s to 0.902s. A criterion whose noise band reaches its threshold
    is a coin flip, and it is a coin flip that reddens row 1 of the chain. The two claims below are
    causal instead: the thread that answered the replay never ran a synthesis, and the replay was
    answered while the job still had no published results -- so a handler that synthesises or waits is
    caught at zero milliseconds. The span is reported, never judged.
    """
    module = load_module(tmp_path)
    written_on = []
    real_write = fake_writer(0.5)

    def writer(path, seed, bpm, ordinal):
        written_on.append(threading.get_ident())
        return real_write(path, seed, bpm, ordinal)

    module.write_wav = writer
    served_on = []
    real_resolve = module.resolve

    def spy_resolve(row):
        served_on.append(threading.get_ident())
        return real_resolve(row)

    module.resolve = spy_resolve
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
    assert served_on, "the replay never reached resolve(), so the thread evidence is missing"
    assert written_on, (
        "no clip was written at all, so the serving-thread rule had nothing to look at -- the fixture's "
        "generation never ran")
    assert not synthesis_on_the_serving_thread(written_on, served_on), (
        f"clip bytes were written on the thread that answered the replay ({elapsed:.3f}s, jobs "
        f"{len(written_on)} writes, serving thread {sorted(set(served_on))}) -- synthesis is back "
        "on the request path"
    )
    # The other half: the replay must have been answered *before* the pool finished, or it waited.
    assert replay.json()["status"] != "completed", (
        f"the replay returned a finished job after {elapsed:.3f}s of eight 0.5s clips that had only "
        "2.2s of head start, so the handler waited for generation instead of answering from the row"
    )
    with module.db() as conn:
        row = conn.execute("SELECT created_at, results_json FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["results_json"] is None, (
        "results were already published when the replay answered, so this replay proves nothing about "
        "waiting -- the fixture's own generation window has moved")
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
