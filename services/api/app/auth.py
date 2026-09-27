import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import psycopg2.extras
from fastapi import Depends, Header, HTTPException, Request

from .db import fetch_all, fetch_one
from .settings import settings

ACCESS_COOKIE = "resonance_access"
REFRESH_COOKIE = "resonance_refresh"
CSRF_COOKIE = "resonance_csrf"


@dataclass(frozen=True)
class UserIdentity:
    user_id: str
    email: str
    display_name: str
    is_platform_admin: bool
    session_id: str | None = None


@dataclass(frozen=True)
class Actor(UserIdentity):
    workspace_id: str = ""
    role: str = ""


def verify_password(password: str, encoded: str) -> bool:
    try:
        alg, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if alg != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def address_hash(client_ip: str) -> str:
    """A keyed digest of a client address, for the one column that has to store one.

    token_hash is right for a bearer token -- 256 bits of randomness make reversal irrelevant -- and
    wrong for an address, whose whole space is 2**32 for IPv4. Peppering it means the stored value
    reveals nothing without `ADDRESS_PEPPER`, so an old session row cannot be turned back into the
    subscriber's home connection after the fact.
    """
    return hashlib.sha256(f"{settings.address_pepper}|{client_ip}".encode()).hexdigest()


def issue_token(user_id: str, session_id: str | None = None, *, amr: list[str] | None = None,
                purpose: str | None = None, ttl_seconds: int | None = None) -> str:
    """Access token. A `purpose` token is NOT an access token -- see decode_pending."""
    now = datetime.now(timezone.utc)
    lifetime = timedelta(seconds=ttl_seconds) if ttl_seconds else timedelta(minutes=settings.jwt_ttl_minutes)
    claims: dict[str, Any] = {
        "sub": user_id,
        "iat": now,
        "nbf": now,
        "exp": now + lifetime,
        "aud": "music-platform-v13",
        "iss": "resonance",
        "jti": secrets.token_hex(16),
    }
    if session_id:
        claims["sid"] = session_id
    if amr:
        claims["amr"] = amr
    if purpose:
        claims["purpose"] = purpose
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_claims(value: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            value,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience="music-platform-v13",
            issuer="resonance",
            options={"require": ["sub", "iat", "exp", "jti"]},
        )
    except Exception:
        raise HTTPException(401, "invalid or expired access token")


def decode_pending(value: str) -> str:
    """The user id behind a second-factor pending token, and nothing else.

    Refusing in both directions is the point: a pending token cannot be spent as an access token
    (checked by purpose here and rejected in get_user), and an access token cannot be presented
    at the challenge endpoint.
    """
    claims = decode_claims(value)
    if claims.get("purpose") != "mfa_pending":
        raise HTTPException(401, "not a pending second-factor token")
    return str(claims["sub"])


def decode_token(value: str) -> tuple[str, str | None]:
    data = decode_claims(value)
    return str(data["sub"]), str(data["sid"]) if data.get("sid") else None


def create_browser_session(cur, user_id: str, user_agent: str, client_ip: str, *,
                           amr: list[str] | None = None,
                           mfa_at: datetime | None = None) -> dict[str, str]:
    """The only place a session is minted. `amr`/`mfa_at` travel with it deliberately: refresh
    rotation revokes a row and creates another (main.py:171-181), so the proof that a second
    factor was presented has to be carried into the replacement or the assurance level of a live
    session changes silently. Both are new columns from 015, which matters because
    005's guard_auth_session_update raises on any change to id, user_id, refresh_token_hash,
    csrf_token_hash, created_at or expires_at -- the trigger that made erasure fail in 013.
    """
    session_id = str(__import__("uuid").uuid4())
    refresh_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_ttl_days)
    cur.execute(
        """
        INSERT INTO auth_sessions(
          id,user_id,refresh_token_hash,csrf_token_hash,user_agent_hash,ip_hash,expires_at,amr,mfa_at
        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            session_id,
            user_id,
            token_hash(refresh_token),
            token_hash(csrf_token),
            token_hash(user_agent or ""),
            address_hash(client_ip or ""),
            expires_at,
            ",".join(amr or ["pwd"]),
            mfa_at,
        ),
    )
    return {
        "session_id": session_id,
        "access_token": issue_token(user_id, session_id, amr=amr or ["pwd"]),
        "refresh_token": refresh_token,
        "csrf_token": csrf_token,
        "expires_at": expires_at.isoformat(),
    }


def validate_refresh_session(refresh_token: str):
    row = fetch_one(
        """
        SELECT s.*,u.status AS user_status
        FROM auth_sessions s JOIN users u ON u.id=s.user_id
        WHERE s.refresh_token_hash=%s
        """,
        (token_hash(refresh_token),),
    )
    if not row or row["revoked_at"] is not None or row["expires_at"] <= datetime.now(timezone.utc) or row["user_status"] != "active":
        raise HTTPException(401, "refresh session is unavailable")
    return row


def request_token(request: Request, authorization: str | None) -> tuple[str | None, bool]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip(), False
    cookie = request.cookies.get(ACCESS_COOKIE)
    return cookie, bool(cookie)


def get_user(request: Request, authorization: str | None = Header(default=None, alias="Authorization")) -> UserIdentity:
    token, from_cookie = request_token(request, authorization)
    if not token:
        raise HTTPException(401, "authentication required")
    claims = decode_claims(token)
    user_id, session_id = str(claims["sub"]), str(claims["sid"]) if claims.get("sid") else None
    if from_cookie and not session_id:
        raise HTTPException(401, "browser session is invalid")
    if session_id:
        session = fetch_one(
            "SELECT id,user_id,expires_at,revoked_at,mfa_at FROM auth_sessions WHERE id=%s",
            (session_id,),
        )
        if (
            not session
            or str(session["user_id"]) != user_id
            or session["revoked_at"] is not None
            or session["expires_at"] <= datetime.now(timezone.utc)
        ):
            raise HTTPException(401, "session has been revoked or expired")
    if claims.get("purpose"):
        raise HTTPException(401, "this credential is not an access token")
    user = fetch_one(
        "SELECT id,email,display_name,status,is_platform_admin,mfa_enrolled_at FROM users WHERE id=%s",
        (user_id,),
    )
    if not user or user["status"] != "active":
        raise HTTPException(401, "user unavailable")
    # Second factor enforcement. A session that never presented one is refused once the account
    # is armed; confirming an enrolment stamps the session that proved the code, so enrolling
    # cannot lock the person who is doing it out mid-flight.
    if user["mfa_enrolled_at"] is not None and not (session_id and session.get("mfa_at")):
        raise HTTPException(401, "second factor required")
    return UserIdentity(str(user["id"]), user["email"], user["display_name"], bool(user["is_platform_admin"]), session_id)


def validate_browser_csrf(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    # /auth/mfa/challenge is the second half of the login handshake, so it is exempt for the same
    # reason /auth/login is: there is no session yet, and a browser that still carries an old
    # access cookie from another account would otherwise be 403'd before it ever got one. The
    # residual is login-CSRF (an attacker who supplies their own pending_token can get the victim's
    # browser a session as the attacker) -- which /auth/login already carries, and which the
    # per-account challenge limiter bounds. Every other MFA endpoint is authenticated and checks.
    if request.url.path in {"/api/auth/login", "/api/auth/refresh", "/api/auth/mfa/challenge",
                            "/api/provider/webhook", "/api/payments/webhook"}:
        return
    if request.headers.get("Authorization", "").lower().startswith("bearer "):
        return
    access = request.cookies.get(ACCESS_COOKIE)
    if not access:
        return
    _, session_id = decode_token(access)
    supplied = request.headers.get("X-CSRF-Token")
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not session_id or not supplied or not cookie_token or not hmac.compare_digest(supplied, cookie_token):
        raise HTTPException(403, "CSRF validation failed")
    session = fetch_one("SELECT csrf_token_hash,revoked_at,expires_at FROM auth_sessions WHERE id=%s", (session_id,))
    if (
        not session
        or session["revoked_at"] is not None
        or session["expires_at"] <= datetime.now(timezone.utc)
        or not hmac.compare_digest(session["csrf_token_hash"], token_hash(supplied))
    ):
        raise HTTPException(403, "CSRF session validation failed")


def get_actor(
    user: UserIdentity = Depends(get_user),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
) -> Actor:
    if not x_workspace_id:
        raise HTTPException(400, "X-Workspace-Id is required")
    row = fetch_one(
        "SELECT m.role,w.status FROM workspace_members m JOIN workspaces w ON w.id=m.workspace_id WHERE m.workspace_id=%s AND m.user_id=%s",
        (x_workspace_id, user.user_id),
    )
    if not row or row["status"] != "active":
        raise HTTPException(403, "workspace membership required")
    return Actor(
        user.user_id,
        user.email,
        user.display_name,
        user.is_platform_admin,
        user.session_id,
        x_workspace_id,
        row["role"],
    )


def require_platform_admin(actor: Actor = Depends(get_actor)):
    if not actor.is_platform_admin:
        raise HTTPException(403, "platform administrator required")
    return actor


def require_roles(*roles: str):
    def dep(actor: Actor = Depends(get_actor)):
        if actor.role not in roles:
            raise HTTPException(403, "insufficient role")
        return actor

    return dep


def list_memberships(user_id: str):
    return fetch_all(
        "SELECT w.id,w.name,w.plan,w.status,m.role FROM workspace_members m JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=%s ORDER BY w.created_at",
        (user_id,),
    )
