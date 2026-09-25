"""Guards on the reason a terminal job carries.

Found by a real red run, not by reading: 13 of 15 failed generation_jobs had error=NULL because
worker.py only copied the provider payload into that column, while the actual cause (an ffprobe
timeout during media ingest) was written on the audio_candidates row. These tests pin the
property that was violated -- a non-completed job always carries a reason -- and the passthrough
behaviour it must not break.
"""
import pathlib
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
