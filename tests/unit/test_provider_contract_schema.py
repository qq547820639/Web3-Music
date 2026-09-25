"""The written provider contract against the code that must honour it.

The repository shipped two provider adapters posting two undocumented shapes: EmulatorAdapter
sends a flat {title,lyrics,styles,bpm,...} body and GenericRESTAdapter sends
{external_request_id,model,candidate_count,song_spec}. Nothing said which one a vendor
implements, and the first proof of that was MUSIC_PROVIDER=generic_rest getting 422 from our
own emulator. So the contract is now written down, and these tests check the client side of it
against the real adapter -- the server side is checked in services/acceptance/test_provider_contract.py.
"""
import json
import pathlib
import sys

import jsonschema
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/worker"))

from provider import GenericRESTAdapter  # noqa: E402

SCHEMA = json.loads((ROOT / "shared/contracts/provider-submit-v1.schema.json").read_text(encoding="utf-8"))
SPEC = {"title": "灯还亮", "lyrics": "末班车把影子拉得很长", "styles": ["piano pop"], "bpm": 84, "mood": ["克制"]}


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("GENERIC_PROVIDER_BASE_URL", "https://provider.example")
    monkeypatch.setenv("GENERIC_PROVIDER_MODEL", "voice-v9")
    return GenericRESTAdapter()


def payload(adapter, job_id="job-1", count=2, spec=None):
    return adapter.submit_payload(job_id, spec if spec is not None else SPEC, count)


def test_the_adapter_payload_satisfies_the_written_contract(adapter):
    jsonschema.validate(payload(adapter), SCHEMA)


def test_contract_declares_exactly_the_keys_the_adapter_sends(adapter):
    assert set(payload(adapter)) == set(SCHEMA["properties"]), "adapter drifted from the contract"


def test_model_is_nullable_and_still_conforms(adapter):
    body = payload(adapter)
    assert body["model"] == "voice-v9"
    import os
    del os.environ["GENERIC_PROVIDER_MODEL"]
    blank = GenericRESTAdapter().submit_payload("job-2", SPEC, 1)
    assert blank["model"] is None
    jsonschema.validate(blank, SCHEMA)


@pytest.mark.parametrize("mutate,why", [
    (lambda b: b.pop("external_request_id"), "the idempotency pairing must be in the body too"),
    (lambda b: b.update(candidate_count=9), "capabilities cap the count at 8"),
    (lambda b: b.update(candidate_count=0), "zero candidates is not a job"),
    (lambda b: b.update(song_spec={"lyrics": "no title"}), "title is required"),
    (lambda b: b.update(song_spec={"title": "ok", "bpm": 12}), "bpm outside the musical range"),
    (lambda b: b.update(vendor_private_field="x"), "additionalProperties=false forbids undeclared keys"),
])
def test_contract_rejects_each_way_it_could_be_broken(adapter, mutate, why):
    body = payload(adapter)
    mutate(body)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, SCHEMA)


def test_the_emulator_native_flat_dialect_is_a_different_contract(adapter):
    # Not a defect, but it must stay visible: the built-in adapter's body would violate the
    # vendor contract, which is precisely why the emulator now names the dialect it received.
    native = {"title": SPEC["title"], "lyrics": SPEC["lyrics"], "styles": "piano pop",
              "bpm": 84, "candidate_count": 2, "scenario": "success"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(native, SCHEMA)
