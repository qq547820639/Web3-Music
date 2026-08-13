import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

from app.domain.deepseek import _mock_patch
from app.domain.patches import PatchError, apply_patch, touches_locked
from app.domain.rights import allowed, build_manifest
from app.storage import sign_media_token, verify_media_token


def sample_spec():
    return {
        "title": "灯还亮",
        "language": "zh-CN",
        "theme": "城市夜归",
        "mood": ["克制", "温暖"],
        "genre": "piano pop ballad",
        "bpm": 72,
        "vocal": {"type": "female", "delivery": "breathy intimate"},
        "hook": "灯还亮",
        "styles": "Mandarin piano pop ballad, intimate vocal, felt piano, warm strings",
        "lyrics": "[Verse]\n雨落在玻璃上\n[Chorus]\n灯还亮 灯还亮\n[Outro]\n[silence 3.0s]",
        "structure": {"sections": ["Verse", "Chorus", "Outro"]},
    }


def test_parent_and_child_json_pointer_locks_are_both_protected():
    assert touches_locked("/vocal/delivery", ["/vocal"])
    assert touches_locked("/vocal", ["/vocal/delivery"])
    with pytest.raises(PatchError):
        apply_patch(sample_spec(), [{"op": "replace", "path": "/vocal", "value": {}}], ["/vocal/delivery"])


def test_array_patch_add_and_remove_are_deterministic():
    spec = sample_spec()
    changed = apply_patch(
        spec,
        [
            {"op": "add", "path": "/mood/-", "value": "释然"},
            {"op": "remove", "path": "/mood/0"},
        ],
    )
    assert changed["mood"] == ["温暖", "释然"]
    assert spec["mood"] == ["克制", "温暖"]


def test_mock_ai_proposer_respects_locked_lyrics_and_preserve_request():
    proposal = _mock_patch(sample_spec(), "保留歌词，编曲更克制", ["/lyrics"])
    assert "/lyrics" in proposal["preserve"]
    assert all(not op["path"].startswith("/lyrics") for op in proposal["operations"])
    assert any(op["path"] == "/styles" for op in proposal["operations"])


def test_emulator_rights_manifest_blocks_commercial_capabilities():
    manifest = build_manifest(
        {"id": "asset-1", "media_hash": "a" * 64},
        {"provider": "emulator", "approval_status": "development_only"},
        {"revision_created": 2},
    )
    assert allowed(manifest, "download")
    assert not allowed(manifest, "commercial_use")
    assert manifest["capabilities"]["license"]["status"] == "blocked"
    assert len(manifest["manifest_hash"]) == 64


def test_media_token_is_bound_to_media_workspace_and_expiry():
    token = sign_media_token("media-1", "workspace-1", ttl=60)
    payload = verify_media_token(token, "media-1")
    assert payload["wid"] == "workspace-1"
    with pytest.raises(ValueError):
        verify_media_token(token, "media-2")
    expired = sign_media_token("media-1", "workspace-1", ttl=-1)
    with pytest.raises(ValueError):
        verify_media_token(expired, "media-1")
