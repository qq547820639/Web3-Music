import hashlib, hmac, io, json, os, time, uuid, zipfile
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

from .auth import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE, Actor, UserIdentity, create_browser_session, get_actor, get_user, list_memberships, require_platform_admin, require_roles, token_hash, validate_browser_csrf, validate_refresh_session, verify_password
from .common import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX, audit, serialize, setting_enabled
from .db import close_pool, fetch_all, fetch_one, transaction, wait_for_db
from .contracts import validate_song_spec
from .settings import settings
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
    memberships=list_memberships(str(row["id"]))
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        session=create_browser_session(cur,str(row["id"]),request.headers.get("User-Agent",""),request.client.host if request.client else "")
        audit(cur,None,"auth.login","user",str(row["id"]),{"email":row["email"],"session_id":session["session_id"]},request.state.request_id)
    _set_session_cookies(response,session)
    return serialize({"access_token":session["access_token"],"token_type":"bearer","csrf_token":session["csrf_token"],"expires_at":session["expires_at"],"user":{"id":row["id"],"email":row["email"],"display_name":row["display_name"]},"workspaces":memberships})


@app.post("/api/auth/refresh")
def refresh(request:Request,response:Response):
    refresh_token=request.cookies.get(REFRESH_COOKIE)
    if not refresh_token: raise HTTPException(401,"refresh cookie required")
    existing=validate_refresh_session(refresh_token)
    with transaction() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("UPDATE auth_sessions SET revoked_at=now(),revoke_reason='rotated' WHERE id=%s AND revoked_at IS NULL",(existing["id"],))
        session=create_browser_session(cur,str(existing["user_id"]),request.headers.get("User-Agent",""),request.client.host if request.client else "")
        audit(cur,None,"auth.refresh","auth_session",str(existing["id"]),{"new_session_id":session["session_id"]},request.state.request_id)
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


@app.get("/api/auth/me")
def me(user:UserIdentity=Depends(get_user)):
    return serialize({"user":user.__dict__,"workspaces":list_memberships(user.user_id)})

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
def list_projects(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), actor: Actor = Depends(get_actor)):
    return serialize(fetch_all("""SELECT p.*,(SELECT count(*) FROM audio_candidates c JOIN generation_jobs j ON j.id=c.job_id WHERE j.project_id=p.id AND c.status='ready') candidate_count,(SELECT a.id FROM asset_snapshots a WHERE a.project_id=p.id ORDER BY a.created_at DESC LIMIT 1) latest_asset_id FROM song_projects p WHERE p.workspace_id=%s ORDER BY p.created_at DESC LIMIT %s OFFSET %s""",(actor.workspace_id,limit,offset),actor.workspace_id))

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
