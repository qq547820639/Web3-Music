"""Second-factor primitives with no database: sealing, TOTP arithmetic, token purposes, codes.

Everything the live drill (scripts/mfa_drill.py) proves by walking the HTTP surface, these tests
pin from the other side: they need no stack, and two of them deliberately check the adapter
against an *independent* implementation rather than against itself.

Which ones those are, and why it matters here:
  * test_code_for_matches_an_independent_rfc6238 re-derives every code from raw hmac/struct
    arithmetic. Asserting pyotp against pyotp would prove nothing about the interval=, digits= and
    step-multiplication choices this repository made inside mfa.code_for().
  * test_rfc6238_appendix_b_vectors checks the dependency against the eighteen values published in
    RFC 6238 Appendix B (read from rfc-editor.org, not from memory: the SHA1 value for T=59 is
    94287082, and the 94290855 that is often quoted for that row is wrong), so a pyotp that
    silently changed its truncation would be caught here rather than inherited.

Both are needed: one pins our use of the library, the other pins the library.
"""
import base64
import dataclasses
import hashlib
import hmac
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

import app.auth as auth  # noqa: E402
import app.mfa as mfa  # noqa: E402
from app.settings import settings  # noqa: E402

KEY_A = "unit-test-sealing-key-aaaa"
KEY_B = "unit-test-sealing-key-bbbb"


def use_key(monkeypatch, value):
    """Settings is a frozen dataclass whose fields are read from the environment at import time,
    so the only honest way to vary the key is to replace the module attribute."""
    monkeypatch.setattr(mfa, "settings", dataclasses.replace(settings, mfa_encryption_key=value))


@pytest.fixture
def keyed(monkeypatch):
    use_key(monkeypatch, KEY_A)
    return mfa


@pytest.fixture
def unkeyed(monkeypatch):
    use_key(monkeypatch, "")
    return mfa


# ---------------------------------------------------------------- sealing ----

def _mfa_drill():
    """Load the live drill for its pure helpers; nothing on the stack is touched."""
    import importlib.util
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("mfa_drill_for_window_rule", ROOT / "scripts/mfa_drill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_refusal_wait_is_compared_to_the_window_within_the_time_that_passed():
    """The live check once used a fixed 2-second tolerance, and a loaded host walked straight through it.

    `acceptance-20260928T192315Z` reddened here with "Retry-After '55' against TTL 51" while a
    `pytest -q tests/unit` of mine competed with the drill on a 4-vCPU VM: the header is read from the
    response, the TTL a `docker exec` round trip later, and the gap grew past the magic number. The rule is
    now directional and clock-bound, so the same pair of readings either way is judged by the seconds that
    actually went by -- and the pair below is the proof that the rule cannot be satisfied by loosening a
    constant.
    """
    drill = _mfa_drill()
    ok_same = drill.retry_matches_window("55", 55, 0.2)
    ok_drift_explained = drill.retry_matches_window("55", 51, 4.0)
    red_drift_unexplained = drill.retry_matches_window("55", 45, 1.0)
    assert ok_same and ok_drift_explained and not red_drift_unexplained
    # The refusal may never promise less than the window it is charged with: that is the direction a
    # re-arming limiter would break, and it must fail no matter how much time passed.
    assert drill.retry_matches_window("50", 55, 30.0) is False
    assert drill.retry_matches_window("", 55, 1.0) is False
    assert drill.retry_matches_window("soon", 55, 1.0) is False
    assert drill.retry_matches_window("61", 60, 1.0) is False
    assert drill.retry_matches_window("0", 55, 1.0) is False
    assert drill.retry_matches_window("-5", 55, 1.0) is False
    # Same three numbers, opposite verdicts: only the measured interval changed.
    assert drill.retry_matches_window("40", 30, 9.5) is True
    assert drill.retry_matches_window("40", 30, 1.5) is False
def test_seal_round_trips(keyed):
    secret = keyed.new_secret()
    assert keyed.unseal(keyed.seal(secret)) == secret


def test_seal_is_randomised_and_every_form_opens(keyed):
    secret = keyed.new_secret()
    first, second = keyed.seal(secret), keyed.seal(secret)
    assert first != second, "a fixed nonce would make identical seeds collide"
    assert keyed.unseal(first) == keyed.unseal(second) == secret


def test_unseal_with_a_different_key_refuses(keyed, monkeypatch):
    secret = keyed.new_secret()
    sealed = keyed.seal(secret)
    use_key(monkeypatch, KEY_B)
    with pytest.raises(Exception):
        mfa.unseal(sealed)


def test_tampering_with_the_ciphertext_refuses(keyed):
    sealed = keyed.seal(keyed.new_secret())
    version, nonce, blob = sealed.split(":")
    flipped = blob[:-2] + ("A" if blob[-2] != "A" else "B") + blob[-1:]
    with pytest.raises(Exception):
        keyed.unseal(":".join([version, nonce, flipped]))


def test_unknown_seal_version_is_refused_rather_than_guessed(keyed):
    sealed = keyed.seal(keyed.new_secret())
    _, nonce, blob = sealed.split(":")
    with pytest.raises(ValueError, match="unsupported seal version"):
        keyed.unseal("v2:" + nonce + ":" + blob)


def test_nothing_seals_without_a_configured_key(unkeyed):
    # The fail-closed half of G10 for this feature: there is no in-file default to fall back on.
    for call in (lambda: unkeyed.seal("JBSWY3DPEHPK3PXP"), lambda: unkeyed.unseal("v1:AAA:AAA"),
                 lambda: unkeyed.hash_recovery("abcdefghjk")):
        with pytest.raises(RuntimeError, match="MFA_ENCRYPTION_KEY"):
            call()


def test_sealed_text_never_contains_the_secret(keyed):
    secret = keyed.new_secret()
    assert secret not in keyed.seal(secret)


# ------------------------------------------------------------------ TOTP ----

def rfc6238(raw_seed: bytes, counter: int, digits: int = 6, algorithm=hashlib.sha1) -> str:
    """The reference arithmetic, written straight from RFC 4226/6238 with no third-party help."""
    digest = hmac.new(raw_seed, counter.to_bytes(8, "big"), algorithm).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def test_code_for_matches_an_independent_rfc6238(keyed):
    secret = keyed.new_secret()
    raw = base64.b32decode(secret)
    assert len(raw) == 20, "pyotp.random_base32(32) must decode to the RFC's 20-byte SHA1 seed"
    for step in (0, 1, 2, 35, 999, 514257, 666171701, 666666670):
        assert keyed.code_for(secret, step) == rfc6238(raw, step), f"step {step}"
        assert len(keyed.code_for(secret, step)) == mfa.TOTP_DIGITS


def test_rfc6238_appendix_b_vectors():
    """The published vectors are 8-digit, so this pins pyotp's truncation, not our digit choice."""
    import pyotp
    keys = {
        hashlib.sha1: b"12345678901234567890",
        hashlib.sha256: b"12345678901234567890123456789012",
        hashlib.sha512: b"1234567890123456789012345678901234567890123456789012345678901234",
    }
    rows = {
        hashlib.sha1: {59: "94287082", 1111111109: "07081804", 1111111111: "14050471",
                       1234567890: "89005924", 2000000000: "69279037", 20000000000: "65353130"},
        hashlib.sha256: {59: "46119246", 1111111109: "68084774", 1111111111: "67062674",
                         1234567890: "91819424", 2000000000: "90698825", 20000000000: "77737706"},
        hashlib.sha512: {59: "90693936", 1111111109: "25091201", 1111111111: "99943326",
                         1234567890: "93441116", 2000000000: "38618901", 20000000000: "47863826"},
    }
    for algorithm, raw in keys.items():
        seed = base64.b32encode(raw).decode()
        for moment, expected in rows[algorithm].items():
            assert pyotp.TOTP(seed, interval=30, digits=8, digest=algorithm).at(moment) == expected
            assert rfc6238(raw, moment // 30, digits=8, algorithm=algorithm) == expected


def test_verify_code_accepts_the_window_and_refuses_outside_it(keyed):
    secret = keyed.new_secret()
    now = 1_760_000_012.0  # mid-step, so the neighbouring steps are both reachable
    step = keyed.current_step(now)
    assert keyed.verify_code(secret, keyed.code_for(secret, step), at=now) == step
    assert keyed.verify_code(secret, keyed.code_for(secret, step - 1), at=now) == step - 1
    assert keyed.verify_code(secret, keyed.code_for(secret, step + 1), at=now) == step + 1
    assert keyed.verify_code(secret, keyed.code_for(secret, step - 2), at=now) is None
    assert keyed.verify_code(secret, keyed.code_for(secret, step + 2), at=now) is None
    # The negative readings above have to be the window doing the work, not a broken comparison:
    # widening it must accept exactly the codes it just refused.
    assert keyed.verify_code(secret, keyed.code_for(secret, step - 2), valid_window=2, at=now) == step - 2


def test_verify_code_refuses_shapes_that_are_not_codes(keyed):
    secret = keyed.new_secret()
    for bad in ("", "      ", "abcdef", "12345", "1234567", "12 34 56"):
        assert keyed.verify_code(secret, bad, at=1_760_000_012.0) is None, bad


def test_provisioning_uri_states_the_parameters_the_verifier_uses(keyed, monkeypatch):
    """An authenticator app reads its clock and code width out of this URI.

    pyotp omits period/digits whenever they equal the Key-Uri-Format defaults and refuses to have
    them passed twice, which would leave the agreement between this module and the app to a
    coincidence: change TOTP_STEP and the URI would still say nothing, so the app keeps generating
    on a 30 second clock and every second factor fails. The second half is the control proving it
    is the module's constants doing the work rather than the defaults.
    """
    secret = keyed.new_secret()
    uri = keyed.provisioning_uri(secret, "probe+work@example.local")
    assert uri.startswith("otpauth://totp/")
    assert "secret=" + secret in uri
    assert "issuer=Resonance" in uri
    assert "probe%2Bwork%40example.local" in uri, "the label must be percent-encoded, not raw"
    assert re.search(r"[?&]period=30(&|$)", uri), uri
    assert re.search(r"[?&]digits=6(&|$)", uri), uri

    monkeypatch.setattr(mfa, "TOTP_STEP", 45)
    monkeypatch.setattr(mfa, "TOTP_DIGITS", 8)
    moved = keyed.provisioning_uri(secret, "probe@example.local")
    assert re.search(r"[?&]period=45(&|$)", moved), moved
    assert re.search(r"[?&]digits=8(&|$)", moved), moved
    assert len(keyed.code_for(secret, 1)) == 8, "the width the verifier checks must move with it"


# ------------------------------------------------------------- recovery ----

def test_recovery_codes_are_stored_as_keyed_hashes_not_plaintext(keyed):
    codes, stored = keyed.new_recovery_codes()
    assert len(codes) == len(stored) == 10
    blob = repr(stored)
    for code in codes:
        assert code not in blob, "a recovery code was stored in the clear"
        assert set(code) <= set(mfa.RECOVERY_ALPHABET), code
        assert len(code) == mfa.RECOVERY_LENGTH
    assert {entry["slot"] for entry in stored} == set(range(1, 11))
    assert all(re.fullmatch(r"[0-9a-f]{64}", entry["hash"]) for entry in stored)
    assert len({entry["hash"] for entry in stored}) == 10


def test_recovery_hash_is_not_a_bare_digest(keyed):
    """If it were, the stored set would be an offline guess over a ~30k-key candidate space."""
    code = "abcdefghjk"
    assert keyed.hash_recovery(code) != hashlib.sha256(code.encode()).hexdigest()
    assert keyed.hash_recovery(code) != hmac.new(KEY_A.encode(), code.encode(), hashlib.sha256).hexdigest()


def test_recovery_hash_depends_on_the_configured_key(keyed, monkeypatch):
    code = "abcdefghjk"
    under_a = keyed.hash_recovery(code)
    use_key(monkeypatch, KEY_B)
    assert mfa.hash_recovery(code) != under_a, "the hashes survive a key change, so they are not keyed"


def test_recovery_hash_refuses_without_a_key(unkeyed):
    with pytest.raises(RuntimeError, match="MFA_ENCRYPTION_KEY"):
        unkeyed.hash_recovery("abcdefghjk")


def test_recovery_codes_are_fresh_every_call(keyed):
    first, _ = keyed.new_recovery_codes()
    second, _ = keyed.new_recovery_codes()
    assert not (set(first) & set(second))


# --------------------------------------------------------- token purposes ----

@pytest.fixture
def jwt(monkeypatch):
    monkeypatch.setattr(auth, "settings", dataclasses.replace(settings, jwt_secret="unit-test-jwt-secret"))
    return auth


def test_pending_token_and_access_token_are_not_interchangeable(jwt):
    pending = jwt.issue_token("user-1", amr=["pwd"], purpose="mfa_pending", ttl_seconds=300)
    access = jwt.issue_token("user-1", "session-1")

    assert jwt.decode_pending(pending) == "user-1"
    with pytest.raises(auth.HTTPException) as refused:
        jwt.decode_pending(access)
    assert refused.value.status_code == 401

    # ...and the other direction, which is the one get_user's enforcement depends on: a pending
    # token carries a purpose, so it must never read as an access token.
    assert jwt.decode_token(access) == ("user-1", "session-1")
    assert jwt.decode_claims(pending)["purpose"] == "mfa_pending"


def test_pending_token_expiry_is_its_own(jwt, monkeypatch):
    """The pending credential outlives nothing: it is minutes wide while the access token is half
    an hour, and shortening one must not silently shorten the other."""
    monkeypatch.setattr(auth, "settings", dataclasses.replace(jwt.settings, jwt_ttl_minutes=30))
    claims = jwt.decode_claims(jwt.issue_token("user-1", purpose="mfa_pending", ttl_seconds=5))
    access = jwt.decode_claims(jwt.issue_token("user-1", "session-1"))
    assert claims["exp"] - claims["iat"] == 5
    assert access["exp"] - access["iat"] == 30 * 60


def test_an_access_token_carries_no_purpose(jwt):
    assert "purpose" not in jwt.decode_claims(jwt.issue_token("user-1", "session-1"))


def test_decode_token_still_returns_the_pair_it_always_returned(jwt):
    """Compatibility pin: every pre-existing caller unpacks two values from this function."""
    assert jwt.decode_token(jwt.issue_token("user-2")) == ("user-2", None)
    with pytest.raises(auth.HTTPException):
        jwt.decode_token("not a token")


# -------------------------------------------------- session minting shape ----

class RecordingCursor:
    def __init__(self):
        self.statement = None
        self.params = None

    def execute(self, sql, params):
        self.statement = " ".join(sql.split())
        self.params = params
        return None


def test_minting_a_session_records_the_factor_proof(jwt):
    """The columns exist because rotation would otherwise drop them; see 015's header comment."""
    from datetime import datetime, timezone
    proof = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    cur = RecordingCursor()
    session = jwt.create_browser_session(cur, "user-3", "UA", "10.0.0.3", amr=["pwd", "totp"], mfa_at=proof)

    assert "amr" in cur.statement and "mfa_at" in cur.statement, cur.statement
    assert cur.params[7] == "pwd,totp"
    assert cur.params[8] is proof
    assert jwt.decode_claims(session["access_token"])["amr"] == ["pwd", "totp"]

    plain = RecordingCursor()
    jwt.create_browser_session(plain, "user-3", "UA", "10.0.0.3")
    assert plain.params[7] == "pwd", "a password-only session must not look second-factored"
    assert plain.params[8] is None
