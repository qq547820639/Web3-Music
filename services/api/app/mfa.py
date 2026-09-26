"""Second-factor material: sealed at rest, verified against a replay guard.

pyotp 2.10.0 is used for the RFC 6238 arithmetic rather than a hand-rolled TOTP: its output was
checked against all 18 Appendix B vectors (sha1/sha256/sha512, each with its own seed) and
against an independent stdlib implementation before being relied on. AESGCM comes from
`cryptography` 50.0.1.

The seed never exists in the clear in the database. It is sealed with a key derived from
MFA_ENCRYPTION_KEY, which deliberately has NO in-file default: with the variable unset these
endpoints fail loudly instead of silently sealing under a value an attacker can guess, which is
the failure mode the settings module already carries for JWT_SECRET and friends. Real key
management (KMS/Secret Manager) is the infra half of the same release-gate line.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .settings import settings

RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no i, l, o, 0, 1: codes are read aloud
RECOVERY_LENGTH = 10
TOTP_STEP = 30
TOTP_DIGITS = 6


def _key() -> bytes:
    secret = settings.mfa_encryption_key
    if not secret:
        raise RuntimeError("MFA_ENCRYPTION_KEY is not configured; second factors are unavailable")
    return hashlib.sha256(b"resonance-mfa-seal-v1 " + secret.encode()).digest()


def new_secret() -> str:
    return pyotp.random_base32(length=32)


def seal(secret_b32: str) -> str:
    nonce = secrets.token_bytes(12)
    blob = AESGCM(_key()).encrypt(nonce, secret_b32.encode(), None)
    return "v1:" + base64.urlsafe_b64encode(nonce).decode() + ":" + base64.urlsafe_b64encode(blob).decode()


def unseal(sealed: str) -> str:
    version, nonce_b64, blob_b64 = sealed.split(":", 2)
    if version != "v1":
        raise ValueError(f"unsupported seal version {version!r}")
    plain = AESGCM(_key()).decrypt(base64.urlsafe_b64decode(nonce_b64), base64.urlsafe_b64decode(blob_b64), None)
    return plain.decode()


def provisioning_uri(secret_b32: str, account: str) -> str:
    """An otpauth:// URI whose stated parameters are the ones verify_code() will use.

    pyotp 2.10.0 will not state them: utils.build_uri drops algorithm/digits/period whenever they
    equal the Key-Uri-Format defaults, and passing them as **kwargs raises "got multiple values for
    keyword argument". That leaves the agreement between this module and the authenticator app to a
    coincidence -- change TOTP_STEP here and the URI still says nothing, so the app carries on
    generating on a 30 second clock and every second factor fails. Both parameters are therefore
    appended here, from the same constants the verifier reads.
    """
    uri = pyotp.TOTP(secret_b32, interval=TOTP_STEP, digits=TOTP_DIGITS,
                     name=account, issuer="Resonance").provisioning_uri()
    for name, value in (("period", TOTP_STEP), ("digits", TOTP_DIGITS)):
        if not re.search(rf"[?&]{name}=", uri):
            uri += f"&{name}={value}"
    return uri


def current_step(at: float | None = None) -> int:
    return int((time.time() if at is None else at) // TOTP_STEP)


def code_for(secret_b32: str, step: int) -> str:
    return pyotp.TOTP(secret_b32, interval=TOTP_STEP, digits=TOTP_DIGITS).at(step * TOTP_STEP)


def verify_code(secret_b32: str, code: str, valid_window: int = 1, at: float | None = None) -> int | None:
    """Return the time step a code matches, or None. Codes are compared, never transformed.

    The caller must still pass the step to accept_mfa_step: matching a window is necessary but
    not sufficient, since without the recorded step a captured code replays for
    valid_window * 30 seconds.
    """
    if not code or not code.isdigit():
        return None
    centre = current_step(at)
    for delta in range(-valid_window, valid_window + 1):
        if hmac.compare_digest(code_for(secret_b32, centre + delta), code):
            return centre + delta
    return None


def new_recovery_codes(count: int = 10) -> tuple[list[str], list[dict]]:
    """Single-use codes. Returns the plaintext (shown once) and what may be stored.

    Only the hash is stored -- not even a short prefix to help identify a used code, because
    three characters of a ten-character code from this alphabet is ~30k candidates, which turns
    the stored set into an offline guess against a value an attacker already has read access to
    if the database is compromised.
    """
    codes, stored = [], []
    for index in range(count):
        code = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_LENGTH))
        codes.append(code)
        stored.append({"slot": index + 1, "hash": hash_recovery(code)})
    return codes, stored


def _recovery_key() -> bytes:
    secret = settings.mfa_encryption_key
    if not secret:
        raise RuntimeError("MFA_ENCRYPTION_KEY is not configured; recovery codes are unavailable")
    return hashlib.sha256(b"resonance-mfa-recovery-v1 " + secret.encode()).digest()


def hash_recovery(code: str) -> str:
    return hmac.new(_recovery_key(), code.encode(), hashlib.sha256).hexdigest()
