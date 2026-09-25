"""The third-party adapter's own surface: what it refuses, what headers it sends, and how it
reads shapes it has never seen from the emulator.

These exist because the generic_rest round-trip step (docker-compose.generic-rest.yml) can only
observe the adapter talking to *our* emulator, which answers in the emulator's shape. The
branches below -- the vendor-shape normalisers and the auth header -- are what a real provider
would actually exercise, and the round trip alone would leave them untested.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/worker"))

from provider import GenericRESTAdapter  # noqa: E402


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("GENERIC_PROVIDER_BASE_URL", "https://provider.example/")
    monkeypatch.setenv("GENERIC_PROVIDER_API_KEY", "sekret")
    monkeypatch.setenv("GENERIC_PROVIDER_MODEL", "voice-v9")
    return GenericRESTAdapter()


def test_missing_base_url_is_a_startup_refusal(monkeypatch):
    monkeypatch.delenv("GENERIC_PROVIDER_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="GENERIC_PROVIDER_BASE_URL is required"):
        GenericRESTAdapter()


def test_trailing_slash_is_stripped_so_paths_do_not_double_up(adapter):
    assert adapter.base_url == "https://provider.example"
    assert adapter.submit_path == "/v1/jobs"


def test_requests_carry_bearer_auth_and_a_per_operation_idempotency_key(adapter):
    plain = adapter._headers(None)
    assert plain["Authorization"] == "Bearer sekret"
    assert "Idempotency-Key" not in plain
    keyed = adapter._headers("job-1")
    assert keyed["Idempotency-Key"] == "job-1"
    # a resubmit must not be able to silently reuse another operation's key
    assert adapter._headers("job-2")["Idempotency-Key"] != keyed["Idempotency-Key"]


def test_auth_header_name_and_prefix_are_configurable(monkeypatch):
    monkeypatch.setenv("GENERIC_PROVIDER_BASE_URL", "https://provider.example")
    monkeypatch.setenv("GENERIC_PROVIDER_API_KEY", "k")
    monkeypatch.setenv("GENERIC_PROVIDER_AUTH_HEADER", "X-Api-Token")
    monkeypatch.setenv("GENERIC_PROVIDER_AUTH_PREFIX", "")
    headers = GenericRESTAdapter()._headers(None)
    assert headers["X-Api-Token"] == "k"
    assert "Authorization" not in headers


def test_no_key_configured_sends_no_auth_header(monkeypatch):
    monkeypatch.setenv("GENERIC_PROVIDER_BASE_URL", "https://provider.example")
    monkeypatch.delenv("GENERIC_PROVIDER_API_KEY", raising=False)
    assert "Authorization" not in GenericRESTAdapter()._headers(None)


@pytest.mark.parametrize("payload", [
    {"id": "a1"}, {"job_id": "a1"}, {"task_id": "a1"},
    {"data": {"id": "a1"}}, {"data": {"job_id": "a1"}},
])
def test_job_identifier_is_found_under_each_vendor_shape(payload):
    assert GenericRESTAdapter._job_id(payload) == "a1"


def test_response_without_any_identifier_is_refused_not_invented():
    with pytest.raises(RuntimeError, match="no job identifier"):
        GenericRESTAdapter._job_id({"status": "completed"})
    with pytest.raises(RuntimeError, match="no job identifier"):
        GenericRESTAdapter._job_id({"id": "", "job_id": None})


@pytest.mark.parametrize("key", ["candidates", "results", "clips"])
def test_candidate_lists_are_found_under_each_vendor_key(key):
    rows = GenericRESTAdapter._candidate_rows({key: [{"audio_url": "https://x/1.mp3"}]})
    assert len(rows) == 1 and rows[0]["audio_url"] == "https://x/1.mp3"


def test_paged_candidate_dict_is_unwrapped():
    for shape in ({"results": {"items": [{"url": "u"}]}}, {"results": {"data": [{"url": "u"}]}}):
        rows = GenericRESTAdapter._candidate_rows(shape)
        assert len(rows) == 1 and rows[0]["audio_url"] == "u"


@pytest.mark.parametrize("row,expected", [
    ({"audioUrl": "u"}, "u"),          # camelCase
    ({"url": "u"}, "u"),               # bare url
    ({"id": 7, "duration_seconds": 12}, None),
])
def test_candidate_field_aliases_and_defaults(row, expected):
    [normalized] = GenericRESTAdapter._candidate_rows({"candidates": [row]})
    assert normalized["audio_url"] == expected
    # a row with audio but no status is completed; a row without audio is still processing
    assert normalized["status"] == ("completed" if expected else "processing")
    assert normalized["duration"] == row.get("duration_seconds")


def test_malformed_candidate_rows_are_skipped_rather_than_raising():
    rows = GenericRESTAdapter._candidate_rows({"candidates": ["a string", 5, {"audio_url": "ok"}]})
    assert [r["audio_url"] for r in rows] == ["ok"]


@pytest.mark.parametrize("vendor,ours", [
    ("succeeded", "completed"), ("partial_success", "partial"), ("error", "failed"),
    ("canceled", "cancelled"), ("running", "processing"), ("queued", "processing"),
])
def test_vendor_status_vocabulary_maps_onto_the_state_machine(adapter, vendor, ours):
    assert adapter.TERMINAL_MAP[vendor] == ours


def test_unknown_status_is_not_silently_treated_as_terminal(adapter):
    assert "sideways" not in adapter.TERMINAL_MAP.values()
