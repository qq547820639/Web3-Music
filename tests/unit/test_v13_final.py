import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The delivery environment used for source verification has no PostgreSQL client.
# A minimal import stub lets us test pure token helpers without opening a database.
psycopg2 = types.ModuleType("psycopg2")
extras = types.ModuleType("psycopg2.extras")
extras.RealDictCursor = type("RealDictCursor", (), {})
extras.Json = lambda value: value
psycopg2.extras = extras
psycopg2.connect = lambda *args, **kwargs: None
sys.modules.setdefault("psycopg2", psycopg2)
sys.modules.setdefault("psycopg2.extras", extras)
sys.path.insert(0, str(ROOT / "services/api"))

from app.auth import decode_token, issue_token, token_hash
from app.domain.moderation import evaluate_policy

sys.path.insert(0, str(ROOT / "services/worker"))
from provider import GenericRESTAdapter


def test_access_token_round_trip_without_session():
    token = issue_token("11111111-1111-1111-1111-111111111111")
    user_id, session_id = decode_token(token)
    assert user_id == "11111111-1111-1111-1111-111111111111"
    assert session_id is None


def test_token_hash_is_stable_and_non_plaintext():
    assert token_hash("secret") == token_hash("secret")
    assert token_hash("secret") != "secret"
    assert len(token_hash("secret")) == 64


def test_moderation_allows_normal_music_brief():
    decision = evaluate_policy({"theme": "城市夜归", "lyrics": "末班车把影子拉得很长"})
    assert decision["status"] == "allow"
    assert decision["reasons"] == []


def test_moderation_reviews_artist_style_reference():
    decision = evaluate_policy({"styles": "模仿某位歌手的声线，做成慢歌"})
    assert decision["status"] in {"review", "block"}
    assert decision["reasons"]


def test_moderation_blocks_voice_clone_request():
    decision = evaluate_policy({"styles": "克隆某人的声音并冒充真人演唱"})
    assert decision["status"] == "block"


def test_generic_provider_extracts_job_id_and_candidates():
    assert GenericRESTAdapter._job_id({"data": {"job_id": "job-1"}}) == "job-1"
    rows = GenericRESTAdapter._candidate_rows(
        {"results": [{"id": "c1", "status": "completed", "audio_url": "https://example.test/a.wav"}]}
    )
    assert rows[0]["id"] == "c1"
    assert rows[0]["audio_url"].endswith("a.wav")
