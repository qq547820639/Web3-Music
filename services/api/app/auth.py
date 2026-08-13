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


def issue_token(user_id: str, session_id: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": user_id,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
        "aud": "music-platform-v13",
        "iss": "resonance",
        "jti": secrets.token_hex(16),
    }
    if session_id:
        claims["sid"] = session_id
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_token(value: str) -> tuple[str, str | None]:
    try:
        data = jwt.decode(
            value,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience="music-platform-v13",
            issuer="resonance",
            options={"require": ["sub", "iat", "exp", "jti"]},
        )
        return str(data["sub"]), str(data["sid"]) if data.get("sid") else None
    except Exception:
        raise HTTPException(401, "invalid or expired access token")


def create_browser_session(cur, user_id: str, user_agent: str, client_ip: str) -> dict[str, str]:
    session_id = str(__import__("uuid").uuid4())
    refresh_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_ttl_days)
    cur.execute(
        """
        INSERT INTO auth_sessions(
          id,user_id,refresh_token_hash,csrf_token_hash,user_agent_hash,ip_hash,expires_at
        ) VALUES(%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            session_id,
            user_id,
            token_hash(refresh_token),
            token_hash(csrf_token),
            token_hash(user_agent or ""),
            token_hash(client_ip or ""),
            expires_at,
        ),
    )
    return {
        "session_id": session_id,
        "access_token": issue_token(user_id, session_id),
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
    user_id, session_id = decode_token(token)
    if from_cookie and not session_id:
        raise HTTPException(401, "browser session is invalid")
    if session_id:
        session = fetch_one(
            "SELECT id,user_id,expires_at,revoked_at FROM auth_sessions WHERE id=%s",
            (session_id,),
        )
        if (
            not session
            or str(session["user_id"]) != user_id
            or session["revoked_at"] is not None
            or session["expires_at"] <= datetime.now(timezone.utc)
        ):
            raise HTTPException(401, "session has been revoked or expired")
    user = fetch_one("SELECT id,email,display_name,status,is_platform_admin FROM users WHERE id=%s", (user_id,))
    if not user or user["status"] != "active":
        raise HTTPException(401, "user unavailable")
    return UserIdentity(str(user["id"]), user["email"], user["display_name"], bool(user["is_platform_admin"]), session_id)


def validate_browser_csrf(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.url.path in {"/api/auth/login", "/api/auth/refresh", "/api/provider/webhook", "/api/payments/webhook"}:
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
