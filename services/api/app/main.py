import hashlib, hmac, io, json, os, re, time, uuid, zipfile
from contextlib import asynccontextmanager
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras
import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import APIKeyCookie, HTTPBearer
from pydantic import BaseModel, Field

from .auth import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE, Actor, UserIdentity, create_browser_session, decode_pending, \
                   get_actor, get_user, issue_token, list_memberships, require_platform_admin, require_roles, token_hash, \
                   validate_browser_csrf, validate_refresh_session, verify_password
from .mfa import hash_recovery as mfa_hash_recovery, new_recovery_codes as mfa_new_recovery_codes, \
    new_secret as mfa_new_secret, provisioning_uri as mfa_provisioning_uri, seal as mfa_seal, unseal as mfa_unseal, \
    verify_code as mfa_verify_code
from .common import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX, audit, serialize, setting_enabled
from .db import close_pool, fetch_all, fetch_one, transaction, wait_for_db
from .contracts import validate_song_spec
from .settings import deny_insecure_defaults, settings
from .storage import client as s3_client, ensure_bucket, presign_get, sign_media_token, verify_media_token
from .domain.deepseek import close_client as close_deepseek_client, propose_patch
from .domain.events import emit
from .domain.ledger import InsufficientCredits, balances as ledger_balances, close_hold, create_hold
from .domain.moderation import evaluate_policy
from .domain.patches import PatchError, apply_patch
from .domain.quality import ENGINE_VERSION, RULESET_VERSION, evaluate
from .domain.rights import allowed as capability_allowed, build_manifest
from .domain.utils import sha256_json
from .metrics import observe_request, render as render_metrics

@asynccontextmanager
async def lifespan(app: FastAPI):
    deny_insecure_defaults()
    wait_for_db(); ensure_bucket(); current_provider_snapshot(); rq.ping()
    yield
    await close_deepseek_client()
    close_pool()

# Declare the two alternative authentication schemes so the exported OpenAPI
# documents securitySchemes (previously empty). auto_error=False keeps these
# as documentation-only; actual enforcement remains in get_user/get_actor.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="JWTBearer", description="JWT access token via Authorization: Bearer <token>")
session_cookie_scheme = APIKeyCookie(name=ACCESS_COOKIE, auto_error=False, scheme_name="SessionCookie", description="HttpOnly browser session cookie (plus X-CSRF-Token on writes)")

app=FastAPI(title="Resonance AI Music Asset Platform",version="13.0.0",lifespan=lifespan,dependencies=[Depends(bearer_scheme),Depends(session_cookie_scheme)])
app.add_middleware(CORSMiddleware,allow_origins=list(settings.cors_origins),allow_credentials=True,allow_methods=["GET","POST","PUT","PATCH","DELETE"],allow_headers=["Authorization","Content-Type","X-Workspace-Id","Idempotency-Key","X-Request-Id","X-CSRF-Token"])
rq=redis.Redis.from_url(settings.redis_url,decode_responses=True)

# Atomic INCR + EXPIRE. Running these as two separate commands leaves a window
# where a crash between them makes the key permanent and locks an email forever.
_login_incr = rq.register_script("local n = redis.call('INCR', KEYS[1]); redis.call('EXPIRE', KEYS[1], ARGV[1]); return n")


@app.middleware("http")
async def request_context(request: Request,call_next):
    request.state.request_id=request.headers.get("X-Request-Id") or str(uuid.uuid4())
    try:
        validate_browser_csrf(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code,content={"detail":exc.detail},headers={"X-Request-Id":request.state.request_id})
    started=time.perf_counter()
    try:
        response=await call_next(request)
    except Exception:
        route_obj=request.scope.get("route")
        route=getattr(route_obj,"path",request.url.path)
        if request.url.path!="/metrics":
            observe_request(request.method,route,500,time.perf_counter()-started)
        raise
    route_obj=request.scope.get("route")
    route=getattr(route_obj,"path",request.url.path)
    if request.url.path!="/metrics":
        observe_request(request.method,route,response.status_code,time.perf_counter()-started)
    response.headers["X-Request-Id"] = request.state.request_id
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="no-referrer"
    return response


@lru_cache(maxsize=1)
def current_provider_snapshot():
    provider=settings.music_provider
    approval=settings.provider_approval_status or "unapproved"
    approved=approval=="approved_commercial" and provider not in {"emulator","suno_community"}
    snapshot={"candidate_count_max":8,"async":True,"supports_custom_lyrics":True,"supports_styles":True,"commercial_rights":"manual_review" if approved else "blocked" if provider=="emulator" else "unknown","supports_cancel":provider in {"emulator","synthetic_licensed","generic_rest"},"supports_webhooks":provider=="generic_rest","contract_required":provider!="emulator"}
    fingerprint=sha256_json({"provider":provider,"approval":approval,"snapshot":snapshot,"adapter_version":settings.provider_adapter_version,"contract_version":settings.provider_contract_version})
    snapshot_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"provider:{fingerprint}"))
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("INSERT INTO provider_capability_snapshots(id,provider,adapter_version,environment,approval_status,snapshot,contract_version) VALUES(%s,%s,%s,'local',%s,%s,%s) ON CONFLICT(id) DO NOTHING",(snapshot_id,provider,settings.provider_adapter_version,approval,psycopg2.extras.Json(snapshot),settings.provider_contract_version))
    return fetch_one("SELECT * FROM provider_capability_snapshots WHERE id=%s",(snapshot_id,))


def ensure_project(project_id:str,actor:Actor,for_update=False,cur=None):
    sql="SELECT * FROM song_projects WHERE id=%s AND workspace_id=%s"+(" FOR UPDATE" if for_update else "")
    if cur:
        cur.execute(sql,(project_id,actor.workspace_id)); row=cur.fetchone()
    else: row=fetch_one(sql,(project_id,actor.workspace_id),actor.workspace_id)
    if not row: raise HTTPException(404,"project not found")
    return row


def get_revision(project_id:str,revision:int|None,actor:Actor,cur=None):
    if revision is None:
        p=ensure_project(project_id,actor,cur=cur); revision=p["current_revision"]
    sql="SELECT * FROM song_spec_revisions WHERE project_id=%s AND revision=%s AND workspace_id=%s"
    if cur: cur.execute(sql,(project_id,revision,actor.workspace_id)); row=cur.fetchone()
    else: row=fetch_one(sql,(project_id,revision,actor.workspace_id),actor.workspace_id)
    if not row: raise HTTPException(404,"revision not found")
    return row

DEFAULT_SPEC={
 "language":"zh-CN","theme":"城市夜归","mood":["克制","温暖"],"genre":"piano pop ballad","bpm":72,
 "vocal":{"type":"female","delivery":"breathy intimate"},"hook":"灯还亮",
 "styles":"Mandarin piano pop ballad, 72 BPM, intimate female vocal, felt piano, warm strings, controlled dynamics, chorus lift, close vocal, small plate reverb, NO aggressive drums",
 "lyrics":"[Verse 1]\n末班车把影子拉得很长\n我在玻璃上看见旧时光\n\n[Chorus]\n灯还亮 灯还亮\n等我把沉默慢慢放\n灯还亮 灯还亮\n回家的路没有遗忘\n\n[Bridge]\n[All Instruments Cut]\n原来我一直把告别戴在身上\n[pause 1.0s]\n今天让它落在风里\n[pause 2.0s]\n\n[Final Chorus]\n我终于知道 灯还亮\n就让旧影留在身旁\n灯还亮 灯还亮\n我继续走向有光的地方\n\n[Outro]\n[silence 3.0s]",
 "structure":{"sections":["Verse 1","Chorus","Bridge","Final Chorus","Outro"]}
}

class LoginBody(BaseModel): email:str; password:str
class ProjectCreate(BaseModel): title:str=Field(min_length=1,max_length=120); spec:dict[str,Any]|None=None
class LockUpdate(BaseModel): locked_paths:list[str]
class PatchRequest(BaseModel): base_revision:int; operations:list[dict[str,Any]]; reason:str="patch"
class ChatRequest(BaseModel): message:str=Field(min_length=1,max_length=4000); base_revision:int|None=None; apply:bool=True
class QuoteRequest(BaseModel): spec_revision:int|None=None; candidate_count:int=Field(default=2,ge=1,le=8); scenario:str="success"
class JobSubmit(BaseModel): quote_id:str; quote_hash:str; user_confirmation:bool
class MasterRequest(BaseModel): candidate_id:str; expected_spec_revision:int; confirmation:bool
class SwitchBody(BaseModel): value:bool

@app.get("/metrics",include_in_schema=False)
def metrics(): return PlainTextResponse(render_metrics(),media_type="text/plain; version=0.0.4")

@app.get("/health")
def health(): return {"status":"ok","version":"13.0.0","quality_engine":ENGINE_VERSION,"ruleset":RULESET_VERSION,"product_layers":["creation_os","asset_os","market_os","intelligence","trust"]}

@app.get("/ready")
def ready():
    try:
        db=fetch_one("SELECT 1 ok")
        rq.ping()
        s3_client().head_bucket(Bucket=settings.s3_bucket)
        return {"status":"ready","database":bool(db["ok"]),"redis":True,"object_storage":True}
    except Exception as exc:
        raise HTTPException(503,{"status":"not_ready","error":str(exc)})

def _set_session_cookies(response: Response, session: dict):
    common={"secure":settings.cookie_secure,"domain":settings.cookie_domain,"samesite":"lax","path":"/"}
    response.set_cookie(ACCESS_COOKIE,session["access_token"],httponly=True,max_age=settings.jwt_ttl_minutes*60,**common)
    response.set_cookie(REFRESH_COOKIE,session["refresh_token"],httponly=True,max_age=settings.refresh_ttl_days*86400,**common)
    response.set_cookie(CSRF_COOKIE,session["csrf_token"],httponly=False,max_age=settings.refresh_ttl_days*86400,**common)


def _clear_session_cookies(response: Response):
    for name in (ACCESS_COOKIE,REFRESH_COOKIE,CSRF_COOKIE):
        response.delete_cookie(name,path="/",domain=settings.cookie_domain,secure=settings.cookie_secure,samesite="lax")


@app.post("/api/auth/login")
def login(body:LoginBody,request:Request,response:Response):
    key=f"login:{hashlib.sha256(body.email.lower().encode()).hexdigest()}"
    attempts=int(_login_incr(keys=[key], args=[60]))
    if attempts>settings.login_rate_limit_per_minute: raise HTTPException(429,"too many login attempts")
    row=fetch_one("SELECT * FROM users WHERE lower(email)=lower(%s)",(body.email,))
    if not row or row["status"]!="active" or not verify_password(body.password,row["password_hash"]): raise HTTPException(401,"invalid credentials")
    if row["mfa_enrolled_at"] is not None:
        # No session and no cookies yet: the password alone must not produce anything a client can
        # mistake for an authenticated credential. The key names below are deliberately the ones
        # /auth/mfa/challenge also returns, so a login helper handles one extra branch instead of
        # a second response shape.
        return serialize({"mfa_required":True,
                          "pending_token":issue_token(str(row["id"]),amr=["pwd"],purpose="mfa_pending",ttl_seconds=settings.mfa_pending_ttl_seconds),
                          "pending_expires_in":settings.mfa_pending_ttl_seconds})
    return _login_response(response,request,row,["pwd"],None)


def _login_response(response: Response, request: Request, row, amr: list[str], mfa_at):
    """Mint the session and build the login body; shared by /auth/login and the challenge."""
    memberships=list_memberships(str(row["id"]))
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        session=create_browser_session(cur,str(row["id"]),request.headers.get("User-Agent",""),request.client.host if request.client else "",amr=amr,mfa_at=mfa_at)
        audit(cur,None,"auth.login","user",str(row["id"]),{"email":row["email"],"session_id":session["session_id"],"amr":amr},request.state.request_id)
    _set_session_cookies(response,session)
    return serialize({"access_token":session["access_token"],"token_type":"bearer","csrf_token":session["csrf_token"],"expires_at":session["expires_at"],"user":{"id":row["id"],"email":row["email"],"display_name":row["display_name"]},"workspaces":memberships})


@app.post("/api/auth/refresh")
def refresh(request:Request,response:Response):
    refresh_token=request.cookies.get(REFRESH_COOKIE)
    if not refresh_token: raise HTTPException(401,"refresh cookie required")
    existing=validate_refresh_session(refresh_token)
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("UPDATE auth_sessions SET revoked_at=now(),revoke_reason='rotated' WHERE id=%s AND revoked_at IS NULL",(existing["id"],))
        # validate_refresh_session returns the whole row, so the factor proof and the method list
        # move to the new session instead of being silently reset to "password only" by rotation.
        amr=(existing["amr"] or "pwd").split(",")
        session=create_browser_session(cur,str(existing["user_id"]),request.headers.get("User-Agent",""),request.client.host if request.client else "",amr=amr,mfa_at=existing["mfa_at"])
        audit(cur,None,"auth.refresh","auth_session",str(existing["id"]),{"new_session_id":session["session_id"],"amr":amr},request.state.request_id)
    _set_session_cookies(response,session)
    return {"access_token":session["access_token"],"csrf_token":session["csrf_token"],"expires_at":session["expires_at"]}


@app.post("/api/auth/logout",status_code=204)
def logout(request:Request,response:Response,user:UserIdentity=Depends(get_user)):
    if user.session_id:
        with transaction() as conn,conn.cursor() as cur:
            cur.execute("UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at,now()),revoke_reason=COALESCE(revoke_reason,'logout') WHERE id=%s",(user.session_id,))
            audit(cur,None,"auth.logout","auth_session",user.session_id,request_id=request.state.request_id)
    _clear_session_cookies(response)
    return None


_mfa_incr = rq.register_script("local n = redis.call('INCR', KEYS[1]); if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end; return n")


def _mfa_throttle(kind: str, subject: str):
    """A fixed window, unlike the login limiter.

    The login counter refreshes its TTL on every attempt (main.py:50 runs EXPIRE
    unconditionally), which is what makes a locked account stay locked while it is being
    prodded; that behaviour is a documented open decision for the owner and is left alone. A
    six-digit code oracle gets the stricter shape from the start: only the first INCR sets the
    expiry, so the window cannot be extended by the attempts it is counting.
    """
    key=f"mfa:{kind}:{hashlib.sha256(subject.encode()).hexdigest()}"
    n=int(_mfa_incr(keys=[key], args=[60]))
    if n>settings.mfa_challenge_rate_limit_per_minute: raise HTTPException(429,"too many second-factor attempts")


def _mfa_account(user_id: str):
    row=fetch_one("SELECT id,email,display_name,status,mfa_enrolled_at,mfa_secret_enc,mfa_recovery FROM users WHERE id=%s",(user_id,))
    if not row or row["status"]!="active": raise HTTPException(401,"user unavailable")
    return row


def _mfa_keyed(action, *args, **kwargs):
    """Run something that needs MFA_ENCRYPTION_KEY and say so instead of 500-ing.

    mfa.py raises RuntimeError when the key is unset rather than sealing under a guessable
    default, which is the fail-closed half of G10. A raw RuntimeError would reach the client as an
    opaque 500, so the two states are separated here: never configured (503, operator problem) and
    configured-but-different (503 from the seal opening, below).
    """
    try:
        return action(*args, **kwargs)
    except RuntimeError as exc:
        raise HTTPException(503,f"second factors are unavailable: {exc}") from exc


def _mfa_pass_code(cur, user_id: str, code: str, secret_enc: str | None) -> str | None:
    """Accept a TOTP code or exactly one recovery code. Returns the method name or None.

    psycopg2's execute() returns None, so the result is fetched as a second statement; there is
    no chained .fetchone() in this repo's other SELECT-of-a-function calls either.
    """
    code=code.strip()
    if code.isdigit():
        if not secret_enc: return None
        try: secret=mfa_unseal(secret_enc)
        except RuntimeError as exc:
            raise HTTPException(503,f"second factors are unavailable: {exc}") from exc
        except Exception as exc:
            # Only the seal opening can fail this way, and it fails when MFA_ENCRYPTION_KEY changed
            # after enrolment. That is an outage for the affected accounts, not a wrong code: saying
            # 401 here would send the user to re-enter a code that can never be accepted.
            raise HTTPException(503,"the stored second factor could not be opened; the sealing key may have changed") from exc
        step=mfa_verify_code(secret, code)
        if step is None: return None
        # The step must be strictly newer than the last accepted one, and that comparison lives
        # in the database, so a captured code cannot be replayed inside its own 30s window and
        # the guard does not depend on which worker process served the request.
        cur.execute("SELECT accept_mfa_step(%s,%s) AS ok",(user_id,step))
        return "totp" if cur.fetchone()["ok"] else None
    recovery_hash=_mfa_keyed(mfa_hash_recovery,code.lower())
    cur.execute("SELECT consume_mfa_recovery_code(%s,%s) AS ok",(user_id,recovery_hash))
    return "recovery" if cur.fetchone()["ok"] else None


class StepUp(BaseModel):
    """Credentials re-presented at the moment of an irreversible write.

    A session cookie proves who authenticated when; it says nothing about whether whoever holds it
    now can still produce the password. That distinction is the whole point of a step-up challenge,
    so the destructive endpoints ask for the credential again instead of trusting the cookie alone.
    Both fields are optional on the model because "absent" must be answerable with a challenge
    (401) rather than a Pydantic 422, which would put the schema in charge of a security decision.
    """
    password: str | None = None
    code: str | None = None


def _step_up_key(user_id: str) -> str:
    return f"stepup:{hashlib.sha256(user_id.encode()).hexdigest()}"


def _step_up_gate(user_id: str) -> None:
    """Refuse while this account's failure window is full.

    Only failed confirmations are counted, and a correct one empties the window: an owner who moves
    through the roster seven times in a minute is doing their job, and a limiter that counted
    attempts would lock them out of their own workspace. Guessing is still bounded -- six wrong
    credentials per minute -- because the counter is what the gate reads.
    """
    if int(rq.get(_step_up_key(user_id)) or 0) >= settings.mfa_challenge_rate_limit_per_minute:
        raise HTTPException(429, "too many failed confirmations; try again in a minute")


def _step_up_failed(user_id: str) -> None:
    # The same fixed-window script the second factor uses: only the first INCR sets the expiry, so
    # the window cannot be stretched by the attempts it is counting.
    _mfa_incr(keys=[_step_up_key(user_id)], args=[60])


def _step_up_refused(request: Request, user_id: str, kind: str, status: int, reason: str, counted: bool = True) -> HTTPException:
    """Record a failed confirmation, then hand back the exception for the caller to raise.

    The refusal is written in its own transaction because the caller's is either not open yet or
    about to be rolled back, and "someone holding a valid session could not re-produce the password"
    is exactly the event an incident review looks for.
    """
    if counted:
        _step_up_failed(user_id)
    with transaction() as conn, conn.cursor() as cur:
        # Recorded against the account, not as "system": the caller did hold a valid session, and the
        # whole point of the row is that someone inside a session could not re-produce its credential.
        audit(cur, Actor(user_id, "", "", False, None, None, "unverified"),
              "auth.step_up.denied", "user", user_id,
              {"kind": kind, "reason": reason}, request.state.request_id)
        conn.commit()
    return HTTPException(status, reason)


def step_up_factors(request: Request, user_id: str, kind: str,
                    password: str | None, code: str | None) -> list[str]:
    """Re-verify the identity behind an authenticated session; return the factors that passed.

    Password always, and the second factor as well for any account that has one armed -- the same
    strength-against-sensitivity ladder Okta describes with acr/amr, carried out with this app's own
    PBKDF2 check and 015's replay-proof code acceptance rather than an IdP.

    Deliberately without a confirmation window: Laravel's password-confirmation middleware stamps the
    session and stops asking for three hours, which is a reasonable UX for a settings page and the
    opposite of what a stolen cookie needs. Here the credential is spent on the action it authorises.
    """
    _step_up_gate(user_id)
    row = fetch_one("SELECT password_hash,mfa_enrolled_at,mfa_secret_enc,status FROM users WHERE id=%s",(user_id,))
    if not row or row["status"] != "active": raise HTTPException(401,"user unavailable")
    armed = row["mfa_enrolled_at"] is not None
    # A credential that was never presented is a challenge, not a failed guess, so it does not
    # consume the window: the client's next request is expected to carry it.
    missing = [name for name, given in (("password", password), ("code", code)) if not given]
    if not armed and "code" in missing: missing.remove("code")
    if missing:
        raise _step_up_refused(request, user_id, kind, 401,
                               "this action needs your " + " and ".join(missing), counted=False)
    if not verify_password(password, row["password_hash"]):
        raise _step_up_refused(request, user_id, kind, 403, "password confirmation failed")
    factors = ["password"]
    if armed:
        # Its own committed transaction on purpose: a recovery code spent here stays spent even if the
        # write this authorises is later refused, which is the direction the risk falls in. The cursor
        # factory matters too: _mfa_pass_code reads the function's result by column name.
        with transaction() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            method = _mfa_pass_code(cur, user_id, code, row["mfa_secret_enc"])
            conn.commit()
        if not method:
            raise _step_up_refused(request, user_id, kind, 403, "second factor confirmation failed")
        factors.append(method)
    rq.delete(_step_up_key(user_id))
    return factors


class MfaChallengeBody(BaseModel): pending_token:str; code:str
class MfaCodeBody(BaseModel): code:str
class MfaDisableBody(StepUp): code: str | None = None


@app.post("/api/auth/mfa/challenge")
def mfa_challenge(body:MfaChallengeBody,request:Request,response:Response):
    user_id=decode_pending(body.pending_token)
    _mfa_throttle("challenge",user_id)
    row=_mfa_account(user_id)
    if row["mfa_enrolled_at"] is None: raise HTTPException(409,"no second factor is armed for this account")
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        method=_mfa_pass_code(cur,user_id,body.code,row["mfa_secret_enc"])
        if not method:
            audit(cur,None,"auth.mfa.challenge_failed","user",user_id,{"method":"recovery" if not body.code.strip().isdigit() else "totp"},request.state.request_id)
            conn.commit()
            raise HTTPException(401,"invalid second factor")
        audit(cur,None,"auth.mfa.challenge_passed","user",user_id,{"method":method},request.state.request_id)
    return _login_response(response,request,row,["pwd",method],datetime.now(timezone.utc))


@app.get("/api/auth/mfa/status")
def mfa_status(user:UserIdentity=Depends(get_user)):
    row=fetch_one("""SELECT mfa_enrolled_at,mfa_secret_issued_at,
                            coalesce(jsonb_array_length(nullif(mfa_recovery,'null'::jsonb)),0) AS codes
                     FROM users WHERE id=%s""",(user.user_id,))
    session=None
    if user.session_id:
        session=fetch_one("SELECT amr,mfa_at FROM auth_sessions WHERE id=%s",(user.session_id,))
    return serialize({"armed":row["mfa_enrolled_at"] is not None,"enrolled_at":row["mfa_enrolled_at"],
                      "pending_since":row["mfa_secret_issued_at"],"recovery_codes_remaining":row["codes"],
                      "session_amr":(session or {}).get("amr"),"session_second_factor_at":(session or {}).get("mfa_at")})


@app.post("/api/auth/mfa/enroll")
def mfa_enroll(request:Request,user:UserIdentity=Depends(get_user)):
    _mfa_throttle("enrol",user.user_id)
    row=_mfa_account(user.user_id)
    if row["mfa_enrolled_at"] is not None: raise HTTPException(409,"a second factor is already armed; disable it first")
    secret=mfa_new_secret()
    sealed=_mfa_keyed(mfa_seal,secret)
    with transaction() as conn,conn.cursor() as cur:
        try:
            cur.execute("SELECT begin_mfa_enrolment(%s,%s)",(user.user_id,sealed))
        except psycopg2.errors.RaiseException as exc:
            conn.rollback()
            raise HTTPException(409,str(exc).strip().splitlines()[0]) from exc
        audit(cur,Actor(user.user_id,user.email,user.display_name,user.is_platform_admin,user.session_id,None,"self"),
              "auth.mfa.enrol_begin","user",user.user_id,{"secret_issued":True},request.state.request_id)
    # The seed is returned so the authenticator app can be shown a QR or typed in; the database
    # only ever holds the sealed form.
    return serialize({"secret":secret,"provisioning_uri":mfa_provisioning_uri(secret,row["email"]),
                      "confirm_within_seconds":settings.mfa_enrolment_window_seconds})


@app.post("/api/auth/mfa/enroll/verify")
def mfa_enroll_verify(body:MfaCodeBody,request:Request,user:UserIdentity=Depends(get_user)):
    _mfa_throttle("verify",user.user_id)
    row=_mfa_account(user.user_id)
    if row["mfa_enrolled_at"] is not None: return serialize({"already_armed":True})
    if row["mfa_secret_enc"] is None: raise HTTPException(409,"start enrolment before confirming it")
    codes,stored=_mfa_keyed(mfa_new_recovery_codes)
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if not _mfa_pass_code(cur,user.user_id,body.code,row["mfa_secret_enc"]):
            conn.commit()
            raise HTTPException(401,"that code does not match the pending second factor")
        try:
            cur.execute("SELECT * FROM confirm_mfa_enrolment(%s,%s::jsonb,%s)",
                        (user.user_id,psycopg2.extras.Json(stored),settings.mfa_enrolment_window_seconds))
            confirmed=cur.fetchone()
        except psycopg2.errors.RaiseException as exc:
            conn.rollback()
            raise HTTPException(409,str(exc).strip().splitlines()[0]) from exc
        if user.session_id:
            # Arming must not lock out the session that just proved the code. amr/mfa_at are not
            # on 005's immutable list, which is what made the first erasure write fail (013->014).
            cur.execute("UPDATE auth_sessions SET mfa_at=now(),amr='pwd,totp' WHERE id=%s",(user.session_id,))
        audit(cur,Actor(user.user_id,user.email,user.display_name,user.is_platform_admin,user.session_id,None,"self"),
              "auth.mfa.enrol_confirm","user",user.user_id,{"armed_at":str(confirmed["armed_at"]),"already_armed":confirmed["already_armed"]},request.state.request_id)
    return serialize({"armed":True,"recovery_codes":codes,"note":"shown once; store them somewhere the phone that holds the authenticator is not"})


@app.post("/api/auth/mfa/disable")
def mfa_disable(body:MfaDisableBody,request:Request,user:UserIdentity=Depends(get_user)):
    _mfa_throttle("disable",user.user_id)
    row=_mfa_account(user.user_id)
    if row["mfa_enrolled_at"] is None: return serialize({"armed":False})
    # Dropping the second factor is the one action an attacker holding a stolen session most wants to
    # take before taking anything else, so a current code alone is no longer enough: step_up_factors
    # asks for the password as well and refuses without it.
    factors = step_up_factors(request, user.user_id, "auth.mfa.disable", body.password, body.code)
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT disable_mfa(%s) AS was_armed",(user.user_id,))
        was=cur.fetchone()["was_armed"]
        audit(cur,Actor(user.user_id,user.email,user.display_name,user.is_platform_admin,user.session_id,None,"self"),
              "auth.mfa.disable","user",user.user_id,{"was_armed":bool(was),"step_up":factors},request.state.request_id)
    return serialize({"armed":False})


# ---- workspace membership (G11) -------------------------------------------------------------
# The API has always been able to read memberships (list_memberships, at auth.py:276) and has never
# had a way to write one: music_app holds SELECT only on workspace_members, so an application UPDATE
# would match zero rows and raise nothing. 017's SECURITY DEFINER functions carry the writes and hold
# the invariants. require_roles() at the edge answers "are you a manager" with a 403, and the
# functions re-derive the same fact from the table because they, not the endpoint, are the guarantee --
# scripts/member_drill.py proves that by calling the functions with a forged actor as music_app.

def _member_write(cur, statement: str, params: tuple):
    """Call one of 017's functions, translating the database's own refusals."""
    try:
        cur.execute(statement, params)
    except psycopg2.errors.RaiseException as exc:
        raise HTTPException(409, str(exc).strip().splitlines()[0]) from exc
    except psycopg2.errors.CheckViolation as exc:
        # Only reachable if the CHECK on role and workspace_role_error() ever disagree, which is
        # exactly the drift the single-source-of-truth choice is meant to make loud rather than quiet.
        raise HTTPException(400, "the workspace role was refused by the database constraint") from exc
    return cur.fetchone()


def _membership_denied(actor: Actor, request: Request, action: str, target: str, reason: str):
    """A refused membership change is a security event, so it is written in its own transaction --
    the caller's is already aborted by the exception that produced the refusal."""
    with transaction() as conn, conn.cursor() as cur:
        audit(cur, actor, action, "workspace_member", target, {"reason": reason}, request.state.request_id)


def _membership_result(row) -> dict:
    """Unwrap SELECT func() FROM ..., whose single jsonb column is named after the function."""
    if not row:
        return {}
    values = list(row.values())
    if len(values) == 1 and isinstance(values[0], dict):
        return values[0]
    return dict(row)


def _membership_change(request: Request, actor: Actor, action: str, statement: str, params: tuple, target: str,
                       step_up: list[str] | None = None):
    # `step_up` is evaluated by the caller before this function runs, so the credential is spent --
    # and a refusal audited -- before any of 017's functions is reached. It is recorded in the audit
    # row only, not in the response: who verified is evidence about the action, not data for the caller.
    with transaction() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            result = _membership_result(_member_write(cur, statement, params))
        except HTTPException as exc:
            _membership_denied(actor, request, action, target, str(exc.detail))
            conn.commit()
            raise
        audit(cur, actor, action, "workspace_member", target,
              {**result, "step_up": step_up} if step_up else result, request.state.request_id)
    return serialize(result)


class MemberBody(BaseModel): email: str; role: str
class MemberRoleBody(StepUp): role: str
class MemberRemoveBody(StepUp): pass
class MemberTransferBody(StepUp): user_id: str


def allowed_workspace_roles() -> list[str]:
    """The legal role names, read out of the CHECK constraint that defines them.

    017 deliberately does not restate the list -- the migration says the copy is the thing that lies --
    and a select box that hardcoded eight names in JavaScript would reintroduce the second source of
    truth that decision removed. An empty result means the constraint could not be identified, and the
    panel reports that instead of offering guesses.
    """
    rows = fetch_all("SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                     "WHERE conrelid='workspace_members'::regclass AND contype='c'")
    defs = [row["definition"] for row in rows if re.search(r"\brole\b", row["definition"])]
    if len(defs) != 1:
        return []
    return re.findall(r"'([a-z_]+)'::\w+", defs[0])


@app.get("/api/workspace/members")
def list_workspace_members(actor: Actor = Depends(get_actor)):
    rows = fetch_all("""SELECT m.user_id,u.email,u.display_name,m.role,m.created_at,u.status AS account_status
                        FROM workspace_members m JOIN users u ON u.id=m.user_id
                        WHERE m.workspace_id=%s ORDER BY m.created_at""", (actor.workspace_id,))
    return serialize({"workspace_id": actor.workspace_id, "members": rows,
                      "actor_role": actor.role, "can_manage": actor.role in {"owner", "admin"} or actor.is_platform_admin,
                      "roles": allowed_workspace_roles()})


@app.post("/api/workspace/members")
def add_workspace_member(body: MemberBody, request: Request, actor: Actor = Depends(require_roles("owner", "admin"))):
    return _membership_change(request, actor, "workspace.member.add",
                              "SELECT * FROM add_workspace_member(%s,%s,%s,%s)",
                              (actor.workspace_id, actor.user_id, body.email, body.role), body.email)


@app.patch("/api/workspace/members/{member_id}")
def change_workspace_member_role(member_id: str, body: MemberRoleBody, request: Request, actor: Actor = Depends(require_roles("owner", "admin"))):
    return _membership_change(request, actor, "workspace.member.role",
                              "SELECT * FROM change_workspace_member_role(%s,%s,%s::uuid,%s)",
                              (actor.workspace_id, actor.user_id, member_id, body.role), member_id,
                              step_up=step_up_factors(request, actor.user_id, "workspace.member.role",
                                                      body.password, body.code))


@app.delete("/api/workspace/members/{member_id}")
def remove_workspace_member(member_id: str, body: MemberRemoveBody, request: Request, actor: Actor = Depends(require_roles("owner", "admin"))):
    return _membership_change(request, actor, "workspace.member.remove",
                              "SELECT * FROM remove_workspace_member(%s,%s,%s::uuid)",
                              (actor.workspace_id, actor.user_id, member_id), member_id,
                              step_up=step_up_factors(request, actor.user_id, "workspace.member.remove",
                                                      body.password, body.code))


@app.post("/api/workspace/members/transfer")
def transfer_workspace_ownership(body: MemberTransferBody, request: Request, actor: Actor = Depends(get_actor)):
    # Deliberately not require_roles("owner"): the refusal that matters -- "only the current owner can
    # transfer ownership" -- is the one that can see both membership rows at once, so it comes from
    # the function. A role check here would answer a different question than the one being asked.
    return _membership_change(request, actor, "workspace.member.transfer",
                              "SELECT * FROM transfer_workspace_ownership(%s,%s,%s::uuid)",
                              (actor.workspace_id, actor.user_id, body.user_id), body.user_id,
                              step_up=step_up_factors(request, actor.user_id, "workspace.member.transfer",
                                                      body.password, body.code))


@app.get("/api/auth/me")
def me(user:UserIdentity=Depends(get_user)):
    return serialize({"user":user.__dict__,"workspaces":list_memberships(user.user_id)})

# (table, actor column, tenant column) triples derived from the migrations' own
# REFERENCES users(id) columns, so a new table that stores a person cannot quietly drop out of
# the export. The tenant column is named per table because the RLS policy guarding each row is
# written against it and the two are not always the same column: brand_submissions is scoped by
# submitting_workspace_id (db/migrations/002_creation_asset_market_os.sql:496), not workspace_id.
# erasure_drill.py checks this list against information_schema, so a migration that adds a user
# key without adding a triple turns the drill red instead of shipping a silently narrower export.
PERSONAL_TABLES=(("song_projects","created_by","workspace_id"),("project_branches","created_by","workspace_id"),
  ("generation_quotes","created_by","workspace_id"),("generation_jobs","created_by","workspace_id"),
  ("master_selections","selected_by","workspace_id"),("project_comments","created_by","workspace_id"),
  ("project_comments","resolved_by","workspace_id"),("rights_evidence","submitted_by","workspace_id"),
  ("rights_evidence","reviewed_by","workspace_id"),("asset_offers","created_by","workspace_id"),
  ("orders","customer_user_id","workspace_id"),("refunds","created_by","workspace_id"),
  ("brand_briefs","created_by","workspace_id"),("brand_submissions","submitted_by","submitting_workspace_id"),
  ("support_tickets","opened_by","workspace_id"),("support_tickets","assigned_to","workspace_id"),
  ("moderation_cases","assigned_to","workspace_id"),("audit_events","actor_id","workspace_id"),
  ("ledger_transactions","created_by","workspace_id"))
# The account record a subject access response is built from. This list, not a SELECT *, so adding
# a column to users cannot silently widen what the endpoint hands out -- and 015's six
# authentication columns are in it deliberately: a sealed seed and a set of code hashes are still
# personal data about the subject, and Art. 15 does not get to omit them because they are unreadable
# without the server's key.
USER_ACCOUNT_COLUMNS=("email","display_name","status","is_platform_admin","created_at","erased_at",
  "mfa_enrolled_at","mfa_secret_issued_at","mfa_secret_enc","mfa_last_step","mfa_recovery",
  "mfa_recovery_generated_at")
# What a subject-access response deliberately does not carry, with the reason stated in the
# payload itself. This exists so the column census in scripts/erasure_drill.py can demand that
# every column of every exported table is either handed over or named here: without a declared
# exclusion list, "the export covers everything" is only true of the columns somebody happened to
# think about when writing the SELECT.
EXPORT_EXCLUSIONS={"password_hash":"a salted PBKDF2 digest is a credential rather than data about "
                                        "the subject, and disclosing it only gives whoever reads the "
                                        "response a new offline cracking target"}
# Every store the export reads, as table.column, declared in the payload so coverage is a fact a
# caller (and the drill) can check rather than an implication of which keys happened to come back.
# The users.* entries are listed individually rather than as "users.*" because the check the drill
# runs is against information_schema: a wildcard would also match a column nobody selected.
EXPORT_COVERAGE=tuple(f"{table}.{column}" for table,column,_ in PERSONAL_TABLES)+(
  "users.id","auth_sessions.user_id","user_preferences.user_id","product_events.user_id","workspace_members.user_id",
  *(f"users.{column}" for column in USER_ACCOUNT_COLUMNS))

@app.get("/api/account/export")
def account_export(user:UserIdentity=Depends(get_user)):
    """Subject access request: everything the platform links to this account."""
    memberships=list_memberships(user.user_id)
    bundle={"exported_at":datetime.now(timezone.utc),"account":fetch_one(
        "SELECT id," + ",".join(USER_ACCOUNT_COLUMNS) + " FROM users WHERE id=%s",(user.user_id,)),
      "coverage":list(EXPORT_COVERAGE),
      "excluded":EXPORT_EXCLUSIONS,
      "workspaces":memberships,
      "sessions":fetch_all("SELECT id,created_at,last_seen_at,expires_at,revoked_at,revoke_reason,user_agent_hash,ip_hash FROM auth_sessions WHERE user_id=%s ORDER BY created_at",(user.user_id,)),
      "preferences":[],
      "events":[],"records":{}}
    for membership in memberships:
        # user_preferences and product_events are FORCE ROW LEVEL SECURITY tables, so an
        # unscoped read of them returns zero rows and raises nothing (measured: the export
        # answered 200 with an empty preferences list while the row was in the table). Every
        # RLS-guarded store in this endpoint is therefore read under its own membership, the
        # same trap 011 and 012 had to solve on the write side.
        bundle["preferences"]+=fetch_all("SELECT workspace_id,scope,project_id,preferences,learning_enabled,updated_at FROM user_preferences WHERE user_id=%s ORDER BY updated_at",(user.user_id,),membership["id"])
        bundle["events"]+=fetch_all("SELECT workspace_id,event_name,event_version,project_id,asset_snapshot_id,properties,occurred_at FROM product_events WHERE user_id=%s ORDER BY occurred_at",(user.user_id,),membership["id"])
        rows={}
        for table,column,tenant in PERSONAL_TABLES:
            found=fetch_all(f"SELECT * FROM {table} WHERE {tenant}=%s AND {column}::text=%s",(membership["id"],user.user_id),membership["id"])
            if found: rows[table+"."+column]=found
        if rows: bundle["records"][membership["id"]]=rows
    return serialize(bundle)

class ErasureBody(StepUp):
    confirmation:str

@app.post("/api/account/erasure")
def account_erasure(body:ErasureBody,request:Request,user:UserIdentity=Depends(get_user)):
    """Right to erasure: identity is anonymised and access is cut, while the accounting
    record the platform is obliged to keep survives under a non-identifying actor id."""
    # The typed-email confirmation below stays, but it is not the check that matters: an email is
    # public information, so on its own it only proves the caller can read the account it is already
    # sitting in. The password is asked for here, in the request that does the erasing.
    factors = step_up_factors(request, user.user_id, "privacy.account.erase", body.password, body.code)
    actor=Actor(user.user_id,user.email,user.display_name,user.is_platform_admin,user.session_id,None,"self")
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute("SELECT erase_user_identity(%s,%s) AS result",(user.user_id,body.confirmation))
            result=cur.fetchone()["result"]
        except psycopg2.errors.RaiseException as exc:
            raise HTTPException(409,str(exc).strip().splitlines()[0]) from exc
        audit(cur,actor,"privacy.account.erase","user",user.user_id,
              {"result":result,"step_up":factors},request.state.request_id)
    return serialize({"status":"erased","detail":result})

@app.get("/api/bootstrap")
def bootstrap(actor:Actor=Depends(get_actor)):
    with transaction(actor.workspace_id) as conn,conn.cursor() as cur: b=ledger_balances(cur,actor.workspace_id)
    provider=current_provider_snapshot()
    n=fetch_one("SELECT count(*) n FROM song_projects WHERE workspace_id=%s",(actor.workspace_id,),actor.workspace_id)["n"]
    return serialize({"actor":actor.__dict__,"credits":b,"project_count":n,"deepseek_configured":bool(settings.deepseek_api_key),"provider":provider,"switches":{k:setting_enabled(k) for k in ("generation_enabled","provider_enabled","exports_enabled","payments_enabled","marketplace_enabled","licenses_enabled","public_sharing_enabled","payouts_enabled","brand_market_enabled")}})

@app.post("/api/projects",status_code=201)
def create_project(body:ProjectCreate,request:Request,actor:Actor=Depends(require_roles("owner","admin","creator"))):
    spec=dict(DEFAULT_SPEC); spec.update(body.spec or {}); spec["title"]=body.title
    validate_song_spec(spec)
    project_id=str(uuid.uuid4()); spec_hash=sha256_json(spec)
    with transaction(actor.workspace_id) as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("INSERT INTO song_projects(id,workspace_id,title,current_revision,created_by) VALUES(%s,%s,%s,1,%s)",(project_id,actor.workspace_id,body.title,actor.user_id))
        cur.execute("INSERT INTO song_spec_revisions(workspace_id,project_id,revision,parent_revision,spec,spec_hash,actor_type,actor_id,reason) VALUES(%s,%s,1,NULL,%s,%s,'user',%s,'project created')",(actor.workspace_id,project_id,psycopg2.extras.Json(spec),spec_hash,actor.user_id))
        cur.execute("INSERT INTO contribution_events(workspace_id,project_id,spec_revision,actor_type,actor_id,event_type,target_paths,evidence) VALUES(%s,%s,1,'user',%s,'project_created','[\"/\"]'::jsonb,%s)",(actor.workspace_id,project_id,actor.user_id,psycopg2.extras.Json({"spec_hash":spec_hash})))
        emit(cur,"SongProjectCreated","song_project",project_id,{"revision":1,"spec_hash":spec_hash},actor.workspace_id)
        audit(cur,actor,"project.create","song_project",project_id,request_id=request.state.request_id)
    return {"id":project_id,"current_revision":1,"spec":spec,"locked_paths":[]}

@app.get("/api/projects")
def list_projects(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), q: str | None = Query(default=None), status: str | None = Query(default=None), actor: Actor = Depends(get_actor)):
    where = "p.workspace_id=%s"
    params: list[Any] = [actor.workspace_id]
    if q:
        where += " AND p.title ILIKE %s"
        params.append(f"%{q}%")
    if status:
        where += " AND p.status=%s"
        params.append(status)
    params += [limit, offset]
    rows = fetch_all(f"""SELECT p.*,(SELECT count(*) FROM audio_candidates c JOIN generation_jobs j ON j.id=c.job_id WHERE j.project_id=p.id AND c.status='ready') candidate_count,(SELECT a.id FROM asset_snapshots a WHERE a.project_id=p.id ORDER BY a.created_at DESC LIMIT 1) latest_asset_id,COUNT(*) OVER()::int AS total FROM song_projects p WHERE {where} ORDER BY p.created_at DESC LIMIT %s OFFSET %s""", tuple(params), actor.workspace_id)
    return serialize({"items": rows, "total": rows[0]["total"] if rows else 0, "limit": limit, "offset": offset})

@app.get("/api/projects/{project_id}")
def project_detail(project_id:str,actor:Actor=Depends(get_actor)):
    project=ensure_project(project_id,actor); revision=get_revision(project_id,None,actor)
    candidates=fetch_all("""SELECT c.*,m.sha256,m.mime_type,m.bytes,m.duration_ms,j.spec_revision,j.status job_status FROM audio_candidates c JOIN generation_jobs j ON j.id=c.job_id LEFT JOIN media_assets m ON m.id=c.media_asset_id WHERE c.workspace_id=%s AND j.project_id=%s ORDER BY c.created_at DESC""",(actor.workspace_id,project_id),actor.workspace_id)
    quality=fetch_one("SELECT * FROM quality_evaluations WHERE workspace_id=%s AND project_id=%s AND spec_revision=%s ORDER BY created_at DESC LIMIT 1",(actor.workspace_id,project_id,project["current_revision"]),actor.workspace_id)
    jobs=fetch_all("SELECT * FROM generation_jobs WHERE workspace_id=%s AND project_id=%s ORDER BY created_at DESC LIMIT 30",(actor.workspace_id,project_id),actor.workspace_id)
    assets=fetch_all("SELECT * FROM asset_snapshots WHERE workspace_id=%s AND project_id=%s ORDER BY created_at DESC",(actor.workspace_id,project_id),actor.workspace_id)
    return serialize({"project":project,"revision":revision,"quality":quality,"candidates":candidates,"jobs":jobs,"assets":assets})

@app.get("/api/projects/{project_id}/revisions")
def revisions(project_id:str,actor:Actor=Depends(get_actor)):
    ensure_project(project_id,actor); return serialize(fetch_all("SELECT * FROM song_spec_revisions WHERE workspace_id=%s AND project_id=%s ORDER BY revision DESC",(actor.workspace_id,project_id),actor.workspace_id))

@app.put("/api/projects/{project_id}/locks")
def update_locks(project_id:str,body:LockUpdate,request:Request,actor:Actor=Depends(require_roles("owner","admin","creator"))):
    normalized=sorted(set(p for p in body.locked_paths if isinstance(p,str) and p.startswith("/")))
    with transaction(actor.workspace_id) as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        ensure_project(project_id,actor,True,cur); cur.execute("UPDATE song_projects SET locked_paths=%s,updated_at=now() WHERE id=%s",(psycopg2.extras.Json(normalized),project_id)); emit(cur,"SongLocksChanged","song_project",project_id,{"locked_paths":normalized},actor.workspace_id); audit(cur,actor,"project.locks.update","song_project",project_id,{"locked_paths":normalized},request.state.request_id)
    return {"locked_paths":normalized}


def apply_revision(cur,project,actor:Actor,base_revision:int,operations:list[dict],reason:str,actor_type="user"):
    if project["current_revision"]!=base_revision: raise HTTPException(409,{"code":"revision_conflict","current_revision":project["current_revision"]})
    cur.execute("SELECT * FROM song_spec_revisions WHERE project_id=%s AND revision=%s",(project["id"],base_revision)); base=cur.fetchone()
    try: spec=apply_patch(base["spec"],operations,project["locked_paths"])
    except PatchError as exc: raise HTTPException(409,{"code":"locked_or_invalid_patch","message":str(exc)})
    validate_song_spec(spec)
    new_revision=base_revision+1; spec_hash=sha256_json(spec)
    try:
        cur.execute("INSERT INTO song_spec_revisions(workspace_id,project_id,revision,parent_revision,spec,spec_hash,command_id,actor_type,actor_id,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(actor.workspace_id,project["id"],new_revision,base_revision,psycopg2.extras.Json(spec),spec_hash,str(uuid.uuid4()),actor_type,actor.user_id,reason))
    except Exception as exc:
        if "unique" in str(exc).lower(): raise HTTPException(409,"revision duplicates an existing specification")
        raise
    cur.execute("UPDATE song_projects SET current_revision=%s,title=%s,updated_at=now() WHERE id=%s",(new_revision,spec.get("title",project["title"]),project["id"]))
    cur.execute("INSERT INTO contribution_events(workspace_id,project_id,spec_revision,actor_type,actor_id,event_type,target_paths,evidence) VALUES(%s,%s,%s,%s,%s,'revision_created',%s,%s)",(actor.workspace_id,project["id"],new_revision,actor_type,actor.user_id,psycopg2.extras.Json([op.get("path") for op in operations]),psycopg2.extras.Json({"operations":operations,"reason":reason})))
    emit(cur,"SongRevisionCreated","song_project",str(project["id"]),{"revision":new_revision,"parent_revision":base_revision,"spec_hash":spec_hash},actor.workspace_id)
    return {"revision":new_revision,"spec":spec,"spec_hash":spec_hash}

@app.post("/api/projects/{project_id}/patch")
def patch_project(project_id:str,body:PatchRequest,request:Request,actor:Actor=Depends(require_roles("owner","admin","creator"))):
    with transaction(actor.workspace_id) as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        project=ensure_project(project_id,actor,True,cur); result=apply_revision(cur,project,actor,body.base_revision,body.operations,body.reason); audit(cur,actor,"revision.patch","song_project",project_id,{"revision":result["revision"]},request.state.request_id)
    return result

@app.post("/api/projects/{project_id}/chat")
async def chat(project_id:str,body:ChatRequest,request:Request,actor:Actor=Depends(require_roles("owner","admin","creator"))):
    project=ensure_project(project_id,actor); revision=get_revision(project_id,body.base_revision,actor)
    started=time.perf_counter()
    proposal=await propose_patch(revision["spec"],body.message,project["locked_paths"],settings.deepseek_api_key,settings.deepseek_base_url,settings.deepseek_model)
    latency_ms=int((time.perf_counter()-started)*1000)
    result=None
    with transaction(actor.workspace_id) as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("INSERT INTO ai_runs(workspace_id,project_id,spec_revision,purpose,provider,model,prompt_version,schema_version,input_hash,output_hash,status,latency_ms,safety,error) VALUES(%s,%s,%s,'song_patch',%s,%s,'song-patch-v1','song-patch-command-v1',%s,%s,%s,%s,%s,%s)",(
            actor.workspace_id,project_id,revision["revision"],proposal.get("source","unknown"),settings.deepseek_model if proposal.get("source")=="deepseek" else "deterministic-local",sha256_json({"spec":revision["spec"],"message":body.message,"locked_paths":project["locked_paths"]}),sha256_json(proposal),"fallback" if proposal.get("warning") or proposal.get("source")=="mock" else "succeeded",latency_ms,psycopg2.extras.Json({"locked_paths_checked":True,"domain_actions_forbidden":True}),psycopg2.extras.Json({"warning":proposal.get("warning")}) if proposal.get("warning") else None
        ))
        if body.apply and proposal.get("operations"):
            locked=ensure_project(project_id,actor,True,cur); result=apply_revision(cur,locked,actor,revision["revision"],proposal["operations"],proposal.get("reason","AI proposal"),"ai"); audit(cur,actor,"ai.patch.apply","song_project",project_id,{"source":proposal.get("source"),"revision":result["revision"]},request.state.request_id)
    return serialize({"proposal":proposal,"applied":result,"latency_ms":latency_ms})

@app.post("/api/projects/{project_id}/quality")
def quality(project_id:str,revision:int|None=Query(default=None),actor:Actor=Depends(require_roles("owner","admin","creator","reviewer"))):
    rev=get_revision(project_id,revision,actor); result=evaluate(rev["spec"]); input_hash=sha256_json(rev["spec"])
    with transaction(actor.workspace_id) as conn,conn.cursor() as cur:
        cur.execute("INSERT INTO quality_evaluations(workspace_id,project_id,spec_revision,engine_version,ruleset_version,input_hash,score,grade,dimensions,variables,risks) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(project_id,spec_revision,engine_version,ruleset_version) DO UPDATE SET score=EXCLUDED.score,grade=EXCLUDED.grade,dimensions=EXCLUDED.dimensions,variables=EXCLUDED.variables,risks=EXCLUDED.risks,input_hash=EXCLUDED.input_hash RETURNING id",(actor.workspace_id,project_id,rev["revision"],ENGINE_VERSION,RULESET_VERSION,input_hash,result["score"],result["grade"],psycopg2.extras.Json(result["dimensions"]),psycopg2.extras.Json(result["variables"]),psycopg2.extras.Json(result["risks"])))
        emit(cur,"QualityEvaluationCompleted","song_project",project_id,{"revision":rev["revision"],"score":result["score"],"grade":result["grade"]},actor.workspace_id)
    return result

@app.post("/api/projects/{project_id}/quotes",status_code=201)
def create_quote(project_id:str,body:QuoteRequest,actor:Actor=Depends(require_roles("owner","admin","creator"))):
    if not setting_enabled("generation_enabled"): raise HTTPException(503,"generation is disabled")
    if body.scenario not in {"success","partial_success","failed","timeout","rate_limited"}: raise HTTPException(400,"unsupported scenario")
    rev=get_revision(project_id,body.spec_revision,actor); provider=current_provider_snapshot(); unit=settings.credits_per_candidate; total=unit*body.candidate_count
    moderation=evaluate_policy(rev["spec"],settings.moderation_policy_version)
    if moderation["status"]=="block": raise HTTPException(422,{"code":"policy_block","decision":moderation})
    expires=datetime.now(timezone.utc)+timedelta(minutes=15); quote_id=str(uuid.uuid4())
    quote={"project_id":project_id,"spec_revision":rev["revision"],"provider_snapshot_id":str(provider["id"]),"provider":provider["provider"],"candidate_count":body.candidate_count,"unit_credits":unit,"total_credits":total,"scenario":body.scenario,"preserved_paths":ensure_project(project_id,actor)["locked_paths"],"rights_summary":{"approval_status":provider["approval_status"],"commercial_use":"blocked" if provider["provider"]=="emulator" else "unknown"},"moderation":moderation,"partial_success_policy":"settle only ready candidates; release the remainder"}
    quote_hash=sha256_json(quote)
    with transaction(actor.workspace_id) as conn,conn.cursor() as cur:
        cur.execute("INSERT INTO moderation_decisions(workspace_id,subject_type,subject_id,policy_version,status,reasons,input_hash) VALUES(%s,'generation_quote',%s,%s,%s,%s,%s)",(actor.workspace_id,quote_id,moderation["policy_version"],moderation["status"],psycopg2.extras.Json(moderation["reasons"]),sha256_json(rev["spec"])))
        cur.execute("INSERT INTO generation_quotes(id,workspace_id,project_id,spec_revision,provider_snapshot_id,candidate_count,unit_credits,total_credits,scenario,quote,quote_hash,expires_at,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(quote_id,actor.workspace_id,project_id,rev["revision"],provider["id"],body.candidate_count,unit,total,body.scenario,psycopg2.extras.Json(quote),quote_hash,expires,actor.user_id))
        emit(cur,"GenerationQuoteCreated","generation_quote",quote_id,{"total_credits":total,"spec_revision":rev["revision"],"moderation_status":moderation["status"]},actor.workspace_id)
    return serialize({"id":quote_id,"quote_hash":quote_hash,"expires_at":expires,"quote":quote})

@app.post("/api/jobs",status_code=202)
def submit_job(body:JobSubmit,request:Request,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),actor:Actor=Depends(require_roles("owner","admin","creator"))):
    if not idempotency_key: raise HTTPException(400,"Idempotency-Key is required")
    if not body.user_confirmation: raise HTTPException(400,"user confirmation is required")
    if not setting_enabled("generation_enabled") or not setting_enabled("provider_enabled"): raise HTTPException(503,"generation/provider disabled")
    request_hash=sha256_json({"body":body.model_dump(),"actor":actor.user_id})
    with transaction(actor.workspace_id) as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM generation_jobs WHERE workspace_id=%s AND idempotency_key=%s",(actor.workspace_id,idempotency_key)); existing=cur.fetchone()
        if existing:
            if existing["request_hash"]!=request_hash: raise HTTPException(409,{"code":"idempotency_conflict","job_id":str(existing["id"])})
            return serialize(existing)
        cur.execute("SELECT * FROM generation_quotes WHERE id=%s AND workspace_id=%s FOR UPDATE",(body.quote_id,actor.workspace_id)); quote=cur.fetchone()
        cur.execute("SELECT * FROM generation_jobs WHERE workspace_id=%s AND idempotency_key=%s",(actor.workspace_id,idempotency_key)); concurrent_existing=cur.fetchone()
        if concurrent_existing:
            if concurrent_existing["request_hash"]!=request_hash: raise HTTPException(409,{"code":"idempotency_conflict","job_id":str(concurrent_existing["id"])})
            return serialize(concurrent_existing)
        cur.execute("SELECT id FROM generation_jobs WHERE quote_id=%s AND workspace_id=%s",(body.quote_id,actor.workspace_id)); quote_job=cur.fetchone()
        if quote_job: raise HTTPException(409,{"code":"quote_already_used","job_id":str(quote_job["id"])})
        if not quote: raise HTTPException(404,"quote not found")
        if quote["quote_hash"]!=body.quote_hash: raise HTTPException(409,"quote hash mismatch")
        if quote["expires_at"]<datetime.now(timezone.utc): raise HTTPException(409,"quote expired")
        job_id=str(uuid.uuid4())
        try: hold_id,_=create_hold(cur,actor.workspace_id,str(quote["id"]),int(quote["total_credits"]),quote["expires_at"]+timedelta(hours=2),actor.user_id)
        except InsufficientCredits as exc: raise HTTPException(402,str(exc))
        cur.execute("INSERT INTO generation_jobs(id,workspace_id,project_id,spec_revision,quote_id,hold_id,provider_snapshot_id,idempotency_key,request_hash,status,requested_candidates,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s) RETURNING *",(job_id,actor.workspace_id,quote["project_id"],quote["spec_revision"],quote["id"],hold_id,quote["provider_snapshot_id"],idempotency_key,request_hash,quote["candidate_count"],actor.user_id)); job=cur.fetchone()
        cur.execute("UPDATE generation_quotes SET accepted_at=COALESCE(accepted_at,now()) WHERE id=%s",(quote["id"],))
        emit(cur,"GenerationJobQueued","generation_job",job_id,{"hold_id":hold_id,"quote_id":str(quote["id"])},actor.workspace_id); audit(cur,actor,"job.submit","generation_job",job_id,{"quote_id":str(quote["id"])},request.state.request_id)
    return serialize(job)

@app.get("/api/jobs")
def list_jobs(actor:Actor=Depends(get_actor)):
    return serialize(fetch_all("SELECT j.*,h.original_amount,h.settled_amount,h.released_amount,h.status hold_status FROM generation_jobs j JOIN credit_holds h ON h.id=j.hold_id WHERE j.workspace_id=%s ORDER BY j.created_at DESC LIMIT 100",(actor.workspace_id,),actor.workspace_id))

@app.get("/api/jobs/{job_id}")
def job_detail(job_id:str,actor:Actor=Depends(get_actor)):
    job=fetch_one("SELECT j.*,h.original_amount,h.settled_amount,h.released_amount,h.status hold_status FROM generation_jobs j JOIN credit_holds h ON h.id=j.hold_id WHERE j.id=%s AND j.workspace_id=%s",(job_id,actor.workspace_id),actor.workspace_id)
    if not job: raise HTTPException(404,"job not found")
    candidates=fetch_all("SELECT c.*,m.sha256,m.mime_type,m.bytes,m.duration_ms FROM audio_candidates c LEFT JOIN media_assets m ON m.id=c.media_asset_id WHERE c.job_id=%s AND c.workspace_id=%s ORDER BY ordinal",(job_id,actor.workspace_id),actor.workspace_id)
    steps=fetch_all("SELECT * FROM generation_steps WHERE job_id=%s AND workspace_id=%s ORDER BY id",(job_id,actor.workspace_id),actor.workspace_id)
    return serialize({"job":job,"candidates":candidates,"steps":steps})

@app.post("/api/candidates/{candidate_id}/media-token")
def candidate_media_token(candidate_id:str,actor:Actor=Depends(get_actor)):
    row=fetch_one("SELECT c.media_asset_id,m.bucket,m.object_key FROM audio_candidates c JOIN media_assets m ON m.id=c.media_asset_id WHERE c.id=%s AND c.workspace_id=%s AND c.status='ready'",(candidate_id,actor.workspace_id),actor.workspace_id)
    if not row: raise HTTPException(404,"ready candidate not found")
    if settings.media_delivery_mode == "presigned" and settings.s3_public_endpoint_url:
        return {"url":presign_get(row["bucket"],row["object_key"]),"expires_in":settings.media_token_ttl_seconds,"delivery":"object_storage"}
    token=sign_media_token(str(row["media_asset_id"]),actor.workspace_id)
    delivery="api_proxy_fallback" if settings.media_delivery_mode=="presigned" else "api_proxy"
    return {"url":f"/media/{row['media_asset_id']}?token={token}","expires_in":settings.media_token_ttl_seconds,"delivery":delivery}

@app.get("/media/{media_id}")
def stream_media(media_id:str,token:str=Query(...)):
    try: payload=verify_media_token(token,media_id)
    except ValueError: raise HTTPException(401,"invalid media token")
    row=fetch_one("SELECT * FROM media_assets WHERE id=%s AND workspace_id=%s",(media_id,payload["wid"]),payload["wid"])
    if not row or row["scan_status"]!="clean": raise HTTPException(404,"media not found")
    obj=s3_client().get_object(Bucket=row["bucket"],Key=row["object_key"])
    return StreamingResponse(obj["Body"].iter_chunks(chunk_size=65536),media_type=row["mime_type"],headers={"Content-Length":str(row["bytes"]),"Cache-Control":"private, max-age=60"})

@app.post("/api/projects/{project_id}/master",status_code=201)
def select_master(project_id:str,body:MasterRequest,request:Request,actor:Actor=Depends(require_roles("owner","admin","creator"))):
    if not body.confirmation: raise HTTPException(400,"confirmation required")
    with transaction(actor.workspace_id) as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        project=ensure_project(project_id,actor,True,cur)
        cur.execute("""SELECT c.*,j.spec_revision,j.provider_snapshot_id,m.sha256 media_hash,m.id media_id,m.bucket,m.object_key FROM audio_candidates c JOIN generation_jobs j ON j.id=c.job_id JOIN media_assets m ON m.id=c.media_asset_id WHERE c.id=%s AND c.workspace_id=%s AND j.project_id=%s AND c.status='ready'""",(body.candidate_id,actor.workspace_id,project_id)); candidate=cur.fetchone()
        if not candidate: raise HTTPException(404,"ready candidate not found")
        if candidate["spec_revision"]!=body.expected_spec_revision: raise HTTPException(409,"candidate revision mismatch")
        cur.execute("SELECT a.id FROM asset_snapshots a WHERE a.workspace_id=%s AND a.project_id=%s AND a.candidate_id=%s",(actor.workspace_id,project_id,candidate["id"])); existing_asset=cur.fetchone()
        if existing_asset: raise HTTPException(409,{"code":"candidate_already_mastered","asset_snapshot_id":str(existing_asset["id"])})
        cur.execute("SELECT id FROM master_selections WHERE workspace_id=%s AND project_id=%s ORDER BY created_at DESC LIMIT 1",(actor.workspace_id,project_id)); previous=cur.fetchone()
        selection_id=str(uuid.uuid4()); cur.execute("INSERT INTO master_selections(id,workspace_id,project_id,candidate_id,spec_revision,selected_by,replaced_selection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",(selection_id,actor.workspace_id,project_id,candidate["id"],candidate["spec_revision"],actor.user_id,previous["id"] if previous else None))
        cur.execute("SELECT * FROM song_spec_revisions WHERE project_id=%s AND revision=%s",(project_id,candidate["spec_revision"])); revision=cur.fetchone()
        snapshot_id=str(uuid.uuid4()); snapshot={"project_id":project_id,"candidate_id":str(candidate["id"]),"spec_revision":candidate["spec_revision"],"spec":revision["spec"],"media":{"id":str(candidate["media_id"]),"sha256":candidate["media_hash"]},"recipe":candidate["recipe"],"selected_by":actor.user_id}
        snapshot_hash=sha256_json(snapshot); cur.execute("INSERT INTO asset_snapshots(id,workspace_id,project_id,master_selection_id,candidate_id,spec_revision,snapshot,snapshot_hash,media_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(snapshot_id,actor.workspace_id,project_id,selection_id,candidate["id"],candidate["spec_revision"],psycopg2.extras.Json(snapshot),snapshot_hash,candidate["media_hash"]))
        cur.execute("SELECT provider,approval_status FROM provider_capability_snapshots WHERE id=%s",(candidate["provider_snapshot_id"],)); provider=cur.fetchone()
        cur.execute("SELECT event_type,count(*) n FROM contribution_events WHERE workspace_id=%s AND project_id=%s GROUP BY event_type",(actor.workspace_id,project_id)); contribution={r["event_type"]:r["n"] for r in cur.fetchall()}
        manifest=build_manifest({"id":snapshot_id,"media_hash":candidate["media_hash"]},{"provider":provider["provider"],"approval_status":provider["approval_status"]},contribution)
        manifest_id=str(uuid.uuid4()); cur.execute("INSERT INTO rights_manifests(id,workspace_id,asset_snapshot_id,version,status,provider,provider_approval,manifest,manifest_hash) VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s)",(manifest_id,actor.workspace_id,snapshot_id,manifest["status"],manifest["provider"],manifest["provider_approval"],psycopg2.extras.Json(manifest),manifest["manifest_hash"]))
        emit(cur,"MasterSelected","asset_snapshot",snapshot_id,{"candidate_id":body.candidate_id,"rights_manifest_id":manifest_id},actor.workspace_id); audit(cur,actor,"master.select","asset_snapshot",snapshot_id,{"candidate_id":body.candidate_id},request.state.request_id)
    return serialize({"asset_snapshot_id":snapshot_id,"snapshot_hash":snapshot_hash,"rights_manifest":manifest})

@app.get("/api/assets/{asset_id}")
def asset_detail(asset_id:str,actor:Actor=Depends(get_actor)):
    asset=fetch_one("SELECT * FROM asset_snapshots WHERE id=%s AND workspace_id=%s",(asset_id,actor.workspace_id),actor.workspace_id)
    if not asset: raise HTTPException(404,"asset not found")
    manifest=fetch_one("SELECT * FROM rights_manifests WHERE asset_snapshot_id=%s AND workspace_id=%s ORDER BY version DESC LIMIT 1",(asset_id,actor.workspace_id),actor.workspace_id)
    return serialize({"asset":asset,"rights":manifest})

@app.get("/api/assets/{asset_id}/export")
def export_asset(asset_id:str,actor:Actor=Depends(get_actor)):
    if not setting_enabled("exports_enabled"): raise HTTPException(503,"exports disabled")
    row=fetch_one("""SELECT a.*,r.manifest,c.recipe,m.bucket,m.object_key,m.mime_type,p.title FROM asset_snapshots a JOIN rights_manifests r ON r.asset_snapshot_id=a.id JOIN audio_candidates c ON c.id=a.candidate_id JOIN media_assets m ON m.id=c.media_asset_id JOIN song_projects p ON p.id=a.project_id WHERE a.id=%s AND a.workspace_id=%s ORDER BY r.version DESC LIMIT 1""",(asset_id,actor.workspace_id),actor.workspace_id)
    if not row: raise HTTPException(404,"asset not found")
    if not capability_allowed(row["manifest"],"download"): raise HTTPException(403,"rights manifest does not allow download")
    audio=s3_client().get_object(Bucket=row["bucket"],Key=row["object_key"])["Body"].read()
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        extension={"audio/wav":"wav","audio/x-wav":"wav","audio/mpeg":"mp3","audio/mp4":"m4a","audio/ogg":"ogg","audio/flac":"flac"}.get(row["mime_type"],"bin")
        z.writestr(f"audio/master.{extension}",audio); z.writestr("metadata/asset-snapshot.json",json.dumps(serialize(row["snapshot"]),ensure_ascii=False,indent=2)); z.writestr("metadata/rights-manifest.json",json.dumps(serialize(row["manifest"]),ensure_ascii=False,indent=2)); z.writestr("metadata/recipe.json",json.dumps(serialize(row["recipe"]),ensure_ascii=False,indent=2)); z.writestr("README.txt","This package records platform evidence. It is not a copyright registration or legal opinion.\n")
    buf.seek(0); filename=f"{row['title']}-asset-{asset_id[:8]}.zip"
    return StreamingResponse(buf,media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="{filename}"'})

@app.get("/api/ledger")
def ledger(actor:Actor=Depends(require_roles("owner","admin","billing","creator"))):
    with transaction(actor.workspace_id) as conn,conn.cursor() as cur: b=ledger_balances(cur,actor.workspace_id)
    holds=fetch_all("SELECT * FROM credit_holds WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 50",(actor.workspace_id,),actor.workspace_id)
    txs=fetch_all("SELECT * FROM ledger_transactions WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 100",(actor.workspace_id,),actor.workspace_id)
    return serialize({"balances":b,"holds":holds,"transactions":txs})

@app.post("/api/provider-webhooks/{provider}")
async def provider_webhook(provider:str,request:Request,x_event_id:str|None=Header(default=None,alias="X-Event-Id"),x_signature:str|None=Header(default=None,alias="X-Signature")):
    raw=await request.body(); expected=hmac.new(settings.provider_webhook_secret.encode(),raw,hashlib.sha256).hexdigest(); valid=bool(x_signature and hmac.compare_digest(x_signature,expected))
    if not valid: raise HTTPException(401,"invalid webhook signature")
    event_id=x_event_id or hashlib.sha256(raw).hexdigest(); payload=json.loads(raw or b"{}")
    with transaction() as conn,conn.cursor() as cur:
        cur.execute("INSERT INTO provider_inbox(provider,event_id,event_type,signature_valid,payload) VALUES(%s,%s,%s,true,%s) ON CONFLICT(provider,event_id) DO NOTHING RETURNING id",(provider,event_id,payload.get("type","status"),psycopg2.extras.Json(payload))); inserted=cur.fetchone()
    return {"accepted":bool(inserted),"duplicate":not bool(inserted),"event_id":event_id}

@app.get("/api/admin/dashboard")
def admin_dashboard(actor:Actor=Depends(require_roles("owner","admin","support","billing","legal"))):
    counts={}
    for name,table in (("projects","song_projects"),("jobs","generation_jobs"),("candidates","audio_candidates"),("assets","asset_snapshots")):
        counts[name]=fetch_one(f"SELECT count(*) n FROM {table} WHERE workspace_id=%s",(actor.workspace_id,),actor.workspace_id)["n"]
    by_status=fetch_all("SELECT status,count(*) n FROM generation_jobs WHERE workspace_id=%s GROUP BY status ORDER BY status",(actor.workspace_id,),actor.workspace_id)
    with transaction(actor.workspace_id) as conn,conn.cursor() as cur: b=ledger_balances(cur,actor.workspace_id)
    audits=fetch_all("SELECT * FROM audit_events WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 100",(actor.workspace_id,))
    return serialize({"counts":counts,"jobs_by_status":by_status,"balances":b,"switches":{k:setting_enabled(k) for k in ("generation_enabled","provider_enabled","exports_enabled","payments_enabled","marketplace_enabled","licenses_enabled","public_sharing_enabled","payouts_enabled","brand_market_enabled")},"audit":audits})

@app.put("/api/admin/switches/{key}")
def update_switch(key:str,body:SwitchBody,request:Request,actor:Actor=Depends(require_platform_admin)):
    if key not in {"generation_enabled","provider_enabled","exports_enabled","payments_enabled","marketplace_enabled","licenses_enabled","public_sharing_enabled","payouts_enabled","brand_market_enabled"}: raise HTTPException(404,"unknown switch")
    with transaction() as conn,conn.cursor() as cur:
        cur.execute("INSERT INTO system_settings(key,value,updated_by) VALUES(%s,%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_by=EXCLUDED.updated_by,updated_at=now()",(key,psycopg2.extras.Json(body.value),actor.user_id)); audit(cur,actor,"system.switch.update","system_setting",key,{"value":body.value},request.state.request_id)
    return {"key":key,"value":body.value}

@app.post("/api/admin/jobs/{job_id}/cancel")
def cancel_job(job_id:str,request:Request,actor:Actor=Depends(require_roles("owner","admin","support"))):
    with transaction(actor.workspace_id) as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM generation_jobs WHERE id=%s AND workspace_id=%s FOR UPDATE",(job_id,actor.workspace_id)); job=cur.fetchone()
        if not job: raise HTTPException(404,"job not found")
        if job["status"] in {"completed","partial","failed","dead_letter","cancelled"}: raise HTTPException(409,"job is terminal")
        cur.execute("UPDATE generation_jobs SET status='cancel_requested',next_attempt_at=now(),error=%s WHERE id=%s",(psycopg2.extras.Json({"code":"cancel_requested_by_admin"}),job_id)); emit(cur,"GenerationCancellationRequested","generation_job",job_id,{"requested_by":actor.user_id},actor.workspace_id); audit(cur,actor,"job.cancel.request","generation_job",job_id,request_id=request.state.request_id)
    return {"id":job_id,"status":"cancel_requested"}


# v13 modular product routers
from .routers.creation import router as creation_router
from .routers.assets import router as assets_router
from .routers.market import router as market_router
from .routers.admin_v12 import router as admin_v12_router
app.include_router(creation_router)
app.include_router(assets_router)
app.include_router(market_router)
app.include_router(admin_v12_router)
