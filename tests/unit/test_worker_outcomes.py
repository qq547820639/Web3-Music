"""Guards on the reason a terminal job carries.

Found by a real red run, not by reading: 13 of 15 failed generation_jobs had error=NULL because
worker.py only copied the provider payload into that column, while the actual cause (an ffprobe
timeout during media ingest) was written on the audio_candidates row. These tests pin the
property that was violated -- a non-completed job always carries a reason -- and the passthrough
behaviour it must not break.
"""
import pathlib
import pytest
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/worker"))

from outcomes import terminal_error

INGEST_ERROR = {"ordinal": 1, "type": "RuntimeError",
                "message": "audio decode validation failed: Command '['ffprobe', ...]' timed out after 15 seconds"}
PROVIDER_ERROR = {"code": "provider_generation_error", "message": "Emulated provider failure"}


def test_completed_job_with_no_provider_error_carries_no_reason():
    assert terminal_error("completed", None, 2, 0, 2) is None


def test_completed_job_passthrough_of_a_provider_error_is_unchanged():
    assert terminal_error("completed", PROVIDER_ERROR, 2, 0, 2) == PROVIDER_ERROR


def test_silent_ingest_failure_now_names_its_cause():
    # Exactly the shape that used to land as status='failed', error=NULL.
    error = terminal_error("failed", None, 0, 1, 1, [INGEST_ERROR])
    assert error["code"] == "no_ready_candidates"
    assert "ffprobe" in error["candidates"][0]["message"]
    assert (error["ready"], error["failed"], error["requested"]) == (0, 1, 1)


def test_partial_result_reports_an_incomplete_candidate_set():
    error = terminal_error("partial", None, 1, 1, 2, [{"ordinal": 2, "code": "missing_provider_candidate"}])
    assert error["code"] == "incomplete_candidate_set"
    assert error["candidates"][0]["code"] == "missing_provider_candidate"


def test_provider_error_keeps_its_own_code_and_gains_the_counts():
    error = terminal_error("failed", PROVIDER_ERROR, 0, 2, 2)
    assert error["code"] == PROVIDER_ERROR["code"]
    assert error["message"] == PROVIDER_ERROR["message"]
    assert (error["ready"], error["failed"], error["requested"]) == (0, 2, 2)


def test_non_dict_provider_error_is_wrapped_not_dropped():
    error = terminal_error("failed", "upstream said no", 0, 1, 1)
    assert error["provider"] == "upstream said no"
    assert error["code"] == "provider_reported_failure"


def test_the_provider_payload_is_not_mutated():
    payload = dict(PROVIDER_ERROR)
    terminal_error("failed", payload, 0, 2, 2)
    assert payload == PROVIDER_ERROR


def test_no_non_completed_outcome_can_return_without_a_reason():
    # The property the defect violated: for every non-completed final and every combination of
    # (provider error, candidate errors), something explainable must reach the job row.
    finals = ("failed", "partial", "dead_letter", "retry_wait")
    combos = [(None, []), (None, [INGEST_ERROR]), (PROVIDER_ERROR, []), ("text", [INGEST_ERROR]), ({}, [])]
    for final in finals:
        for provider_error, reasons in combos:
            error = terminal_error(final, provider_error, 0, 1, 1, reasons)
            assert error, (final, provider_error, reasons)
            assert error.get("code") or error.get("provider"), (final, error)


# ---------------------------------------------------------------------------
# The second attribution gap, found the same way: a 100-run regression settled 98/100 with two
# candidates carrying {"type": "ReadError", "message": ""}. httpx builds transport errors with an
# empty message, so the row recorded a failure that could not be read. error_signature has to make
# that state impossible, and fetch_media_with_retry has to stop costing a user their candidate for a
# connection that closed between requests.

import asyncio  # noqa: E402

import httpx  # noqa: E402

import worker  # noqa: E402
from outcomes import error_signature  # noqa: E402


def read_error_with_cause():
    error = httpx.ReadError("")
    error.__cause__ = ConnectionResetError(54, "Connection reset by peer")
    return error


def test_an_empty_message_stops_beating_as_no_reason():
    signature = error_signature(read_error_with_cause())
    assert signature["type"] == "ReadError"
    assert signature["message"] == "ReadError", signature
    assert signature["cause"] == "ConnectionResetError: [Errno 54] Connection reset by peer", signature


def test_a_normal_message_is_kept_verbatim():
    signature = error_signature(RuntimeError("unexpected media type text/html"))
    assert signature == {"type": "RuntimeError", "message": "unexpected media type text/html"}


def test_the_terminal_record_still_carries_a_reason_for_a_bare_transport_error():
    """The property the whole file exists for, on the shape that used to defeat it."""
    error = terminal_error("failed", None, 0, 1, 1, [{"ordinal": 1, **error_signature(read_error_with_cause())}])
    assert error["code"] == "no_ready_candidates"
    assert error["candidates"][0]["cause"].startswith("ConnectionResetError"), error


def test_a_transient_transport_failure_is_retried_and_the_backoff_grows(monkeypatch):
    attempts = {"n": 0}
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    async def flaky(url):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise read_error_with_cause()
        return "/tmp/x", "sha", "audio/wav", 4096, 8000

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(worker, "fetch_media", flaky)
    assert asyncio.run(worker.fetch_media_with_retry("http://provider/media/x.wav")) == ("/tmp/x", "sha", "audio/wav", 4096, 8000)
    assert attempts["n"] == 3, "the transport error was not retried"
    assert slept == [0.5, 1.0], slept


def test_a_decision_is_not_retried(monkeypatch):
    """A wrong content type is an answer, not a race; retrying it would only hide it slower."""
    attempts = {"n": 0}

    async def wrong_type(url):
        attempts["n"] += 1
        raise RuntimeError("unexpected media type text/html")

    async def no_sleep(seconds):
        raise AssertionError("a non-transport failure must not back off")

    monkeypatch.setattr(worker.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(worker, "fetch_media", wrong_type)
    with pytest.raises(RuntimeError, match="unexpected media type"):
        asyncio.run(worker.fetch_media_with_retry("http://provider/media/x.html"))
    assert attempts["n"] == 1


def test_exhausted_attempts_say_so_and_carry_the_underlying_error(monkeypatch):
    async def always_resets(url):
        raise read_error_with_cause()

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(worker.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(worker, "fetch_media", always_resets)
    with pytest.raises(RuntimeError) as refused:
        asyncio.run(worker.fetch_media_with_retry("http://provider/media/x.wav"))
    message = str(refused.value)
    assert f"after {worker.MEDIA_TRANSPORT_ATTEMPTS} transport attempts" in message, message
    assert "Connection reset by peer" in message, message
    assert isinstance(refused.value.__cause__, httpx.ReadError)


def test_the_retry_count_is_at_least_one_so_the_helper_cannot_silently_skip_every_download(monkeypatch):
    attempts = {"n": 0}

    async def once(url):
        attempts["n"] += 1
        return "/tmp/x", "sha", "audio/wav", 4096, 8000

    monkeypatch.setattr(worker, "MEDIA_TRANSPORT_ATTEMPTS", 1)
    monkeypatch.setattr(worker, "fetch_media", once)
    asyncio.run(worker.fetch_media_with_retry("http://provider/media/x.wav"))
    assert attempts["n"] == 1
