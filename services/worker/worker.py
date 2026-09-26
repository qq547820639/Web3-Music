import asyncio, hashlib, ipaddress, json, mimetypes, os, socket, subprocess, tempfile, threading, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import boto3, httpx, psycopg2, psycopg2.extras, redis
from psycopg2.pool import ThreadedConnectionPool
from botocore.client import Config
from provider import close_http_client as close_provider_http_client, create_adapter
from ledger import close_hold
from outcomes import error_signature, terminal_error
from metrics import inc as metric_inc, set_gauge, start_server as start_metrics_server

DATABASE_URL=os.getenv("DATABASE_URL","postgresql://music_worker:music_worker@localhost:54329/music")
REDIS_URL=os.getenv("REDIS_URL","redis://localhost:63799/0")
S3_ENDPOINT=os.getenv("S3_ENDPOINT_URL","http://localhost:9000")
S3_BUCKET=os.getenv("S3_BUCKET","music-assets")
S3_ACCESS=os.getenv("S3_ACCESS_KEY","minioadmin");S3_SECRET=os.getenv("S3_SECRET_KEY","minioadmin")
WORKER_ID=os.getenv("WORKER_ID",f"worker-{uuid.uuid4().hex[:8]}")
LEASE_SECONDS=int(os.getenv("LEASE_SECONDS","30"));POLL_WINDOW=int(os.getenv("POLL_WINDOW_SECONDS","600"));MAX_MEDIA=int(os.getenv("MAX_MEDIA_BYTES","52428800"))
WORKER_CONCURRENCY=max(1,int(os.getenv("WORKER_CONCURRENCY","8")))
WORKER_DB_POOL_MIN=max(1,int(os.getenv("WORKER_DB_POOL_MIN","2")))
WORKER_DB_POOL_MAX=max(WORKER_DB_POOL_MIN,int(os.getenv("WORKER_DB_POOL_MAX",str(max(12,WORKER_CONCURRENCY+4)))))
DB_CONNECT_TIMEOUT=max(1,int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS","5")))
DB_STATEMENT_TIMEOUT_MS=max(1000,int(os.getenv("DB_STATEMENT_TIMEOUT_MS","15000")))
CLAIM_IDLE_SLEEP=max(0.05,float(os.getenv("CLAIM_IDLE_SLEEP_SECONDS","0.35")))
PROVIDER_POLL_INTERVAL=max(0.5,float(os.getenv("PROVIDER_POLL_INTERVAL_SECONDS","3.0")))
PROVIDER_POLL_MAX_INTERVAL=max(PROVIDER_POLL_INTERVAL,float(os.getenv("PROVIDER_POLL_MAX_INTERVAL_SECONDS","10.0")))
PROVIDER_ADAPTER_VERSION=os.getenv("PROVIDER_ADAPTER_VERSION","3.0.0")
TRUSTED_INTERNAL={x.strip() for x in os.getenv("TRUSTED_INTERNAL_MEDIA_HOSTS","provider-emulator").split(",") if x.strip()}
rq=redis.Redis.from_url(REDIS_URL,decode_responses=True)
default_adapter,default_provider_name=create_adapter()
s3=boto3.client("s3",endpoint_url=S3_ENDPOINT,aws_access_key_id=S3_ACCESS,aws_secret_access_key=S3_SECRET,config=Config(signature_version="s3v4"),region_name="us-east-1")
_media_http_client=None

class Retryable(Exception):pass

def media_http_client():
    global _media_http_client
    if _media_http_client is None:
        max_connections=max(20,int(os.getenv("MEDIA_HTTP_MAX_CONNECTIONS",str(max(32,WORKER_CONCURRENCY*4)))))
        _media_http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(45,connect=10),follow_redirects=False,
            limits=httpx.Limits(max_connections=max_connections,max_keepalive_connections=max_connections),
        )
    return _media_http_client

async def close_media_http_client():
    global _media_http_client
    if _media_http_client is not None:
        await _media_http_client.aclose();_media_http_client=None

_db_pool=None
_db_pool_lock=threading.Lock()

def db_pool():
    global _db_pool
    if _db_pool is None:
        with _db_pool_lock:
            if _db_pool is None:
                _db_pool=ThreadedConnectionPool(
                    WORKER_DB_POOL_MIN,WORKER_DB_POOL_MAX,dsn=DATABASE_URL,
                    connect_timeout=DB_CONNECT_TIMEOUT,
                    options=f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}",
                    application_name="resonance-worker",
                )
    return _db_pool

class PooledConnection:
    def __init__(self):
        self._pool=db_pool();self._conn=self._pool.getconn();self._broken=False
        # jsonb columns must come back as dicts (not JSON strings) so load_job's
        # spec/quote and step output/error are usable without json.loads(). The
        # API registers the same typecaster; the worker had its own pool and
        # was missing it, so EmulatorAdapter.submit(spec.get(...)) raised
        # AttributeError on a str and crashed the whole worker.
        psycopg2.extras.register_default_jsonb(self._conn, loads=json.loads)
    def __getattr__(self,name):return getattr(self._conn,name)
    def __enter__(self):return self
    def __exit__(self,exc_type,exc,tb):
        try:
            if exc_type is not None:self._conn.rollback()
            else:self._conn.commit()
        except Exception:
            self._broken=True
        finally:
            try:
                if not self._conn.closed:self._conn.rollback()
            except Exception:
                self._broken=True
            self._pool.putconn(self._conn,close=self._broken or bool(self._conn.closed))
        return False

def connect():return PooledConnection()

def close_db_pool():
    global _db_pool
    with _db_pool_lock:
        if _db_pool is not None:
            _db_pool.closeall();_db_pool=None

def emit(cur,workspace,event_type,aggregate_id,payload):cur.execute("INSERT INTO domain_outbox(id,workspace_id,aggregate_type,aggregate_id,event_type,payload) VALUES(%s,%s,'generation_job',%s,%s,%s)",(str(uuid.uuid4()),workspace,aggregate_id,event_type,psycopg2.extras.Json(payload)))
def ensure_bucket():
    try:s3.head_bucket(Bucket=S3_BUCKET)
    except Exception:
        try:s3.create_bucket(Bucket=S3_BUCKET)
        except Exception:s3.head_bucket(Bucket=S3_BUCKET)

def claim_job():
    with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id FROM generation_jobs WHERE
          ((status IN ('queued','retry_wait') AND next_attempt_at<=now()) OR
           (status='cancel_requested' AND next_attempt_at<=now() AND (lease_owner IS NULL OR lease_expires_at<now())) OR
           (status IN ('leased','submitting','provider_queued','processing','ingesting') AND lease_expires_at<now()))
          ORDER BY next_attempt_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1""")
        row=cur.fetchone()
        if not row:return None
        cur.execute("""UPDATE generation_jobs SET status=CASE WHEN status='cancel_requested' THEN 'cancel_requested' ELSE 'leased' END,lease_owner=%s,lease_expires_at=now()+(%s||' seconds')::interval,heartbeat_at=now(),attempt_count=attempt_count+1,started_at=COALESCE(started_at,now()) WHERE id=%s RETURNING *""",(WORKER_ID,LEASE_SECONDS,row["id"]));job=cur.fetchone()
        cur.execute("INSERT INTO generation_attempts(workspace_id,job_id,attempt,lease_owner,outcome) VALUES(%s,%s,%s,%s,'started') ON CONFLICT(job_id,attempt) DO NOTHING",(job["workspace_id"],job["id"],job["attempt_count"],WORKER_ID))
        conn.commit();metric_inc("ai_music_worker_jobs_claimed_total");return job

def load_job(job_id):
    with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT j.*,q.unit_credits,q.scenario,q.quote,r.spec,p.provider,p.approval_status FROM generation_jobs j JOIN generation_quotes q ON q.id=j.quote_id JOIN song_spec_revisions r ON r.project_id=j.project_id AND r.revision=j.spec_revision JOIN provider_capability_snapshots p ON p.id=j.provider_snapshot_id WHERE j.id=%s""",(job_id,));return cur.fetchone()

async def heartbeat(job_id,stop:asyncio.Event):
    while not stop.is_set():
        await asyncio.sleep(max(2,LEASE_SECONDS//3))
        try:
            with connect() as conn,conn.cursor() as cur:
                cur.execute("UPDATE generation_jobs SET heartbeat_at=now(),lease_expires_at=now()+(%s||' seconds')::interval WHERE id=%s AND lease_owner=%s AND status NOT IN ('completed','partial','failed','dead_letter','cancelled')",(LEASE_SECONDS,job_id,WORKER_ID));conn.commit()
                if cur.rowcount!=1:stop.set()
        except Exception as exc:print(f"heartbeat {job_id}: {exc}",flush=True)

def set_job(job_id,status,**fields):
    allowed={"provider_job_id","provider_status","error","actual_provider_cost","settled_credits","completed_at","next_attempt_at","lease_owner","lease_expires_at","heartbeat_at"}
    parts=["status=%s"];values=[status]
    for k,v in fields.items():
        if k not in allowed:continue
        parts.append(f"{k}=%s");values.append(psycopg2.extras.Json(v) if k=="error" and isinstance(v,dict) else v)
    values.extend([job_id,WORKER_ID])
    with connect() as conn,conn.cursor() as cur:
        cur.execute("UPDATE generation_jobs SET "+",".join(parts)+" WHERE id=%s AND lease_owner=%s AND status NOT IN ('cancel_requested','cancelled')",tuple(values));conn.commit()

def step(job,step_name,operation_key,status="started",output=None,error=None):
    with connect() as conn,conn.cursor() as cur:
        cur.execute("""INSERT INTO generation_steps(workspace_id,job_id,step_name,operation_key,status,attempt,output,error,ended_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s IN ('completed','failed','compensated') THEN now() END) ON CONFLICT(job_id,operation_key) DO UPDATE SET status=EXCLUDED.status,output=EXCLUDED.output,error=EXCLUDED.error,ended_at=EXCLUDED.ended_at""",(job["workspace_id"],job["id"],step_name,operation_key,status,job["attempt_count"],psycopg2.extras.Json(output or {}),psycopg2.extras.Json(error) if error else None,status));conn.commit()

def validate_url(url:str):
    p=urlparse(url)
    if p.scheme not in {"http","https"} or not p.hostname:raise RuntimeError("unsupported media URL")
    host=p.hostname.lower()
    if host in TRUSTED_INTERNAL:return
    if p.scheme!="https":raise RuntimeError("external media must use HTTPS")
    for info in socket.getaddrinfo(host,p.port or 443,type=socket.SOCK_STREAM):
        addr=ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved:raise RuntimeError("media URL resolves to prohibited network")

async def fetch_media(url:str):
    current=url
    tmp=tempfile.NamedTemporaryFile(delete=False,suffix=Path(urlparse(url).path).suffix or ".bin")
    tmp_path=tmp.name
    tmp.close()
    size=0
    sha=hashlib.sha256()
    mime="application/octet-stream"
    try:
        client=media_http_client()
        for _ in range(4):
            validate_url(current)
            async with client.stream("GET",current,headers={"User-Agent":"resonance-media-ingest/14"}) as response:
                if response.status_code in {301,302,303,307,308}:
                    location=response.headers.get("location")
                    if not location:
                        raise RuntimeError("redirect without location")
                    current=urljoin(current,location)
                    continue
                response.raise_for_status()
                mime=response.headers.get("content-type","application/octet-stream").split(";",1)[0].lower()
                if not (mime.startswith("audio/") or current.lower().endswith((".wav",".mp3",".m4a",".ogg",".flac"))):
                    raise RuntimeError(f"unexpected media type {mime}")
                with open(tmp_path,"wb") as f:
                    async for chunk in response.aiter_bytes(65536):
                        size+=len(chunk)
                        if size>MAX_MEDIA:
                            raise RuntimeError("media exceeds maximum size")
                        sha.update(chunk)
                        f.write(chunk)
                break
        else:
            raise RuntimeError("too many redirects")
        if size<1024:
            raise RuntimeError("media is too small")
        try:
            proc=await asyncio.to_thread(
                subprocess.run,
                ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",tmp_path],
                capture_output=True,text=True,timeout=15,check=True,
            )
            duration_value=float(proc.stdout.strip())
            if duration_value<=0:
                raise ValueError("non-positive audio duration")
            duration_ms=int(duration_value*1000)
        except Exception as exc:
            raise RuntimeError(f"audio decode validation failed: {exc}") from exc
        return tmp_path,sha.hexdigest(),mime,size,duration_ms
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

MEDIA_TRANSPORT_ATTEMPTS = max(1, int(os.getenv("MEDIA_TRANSPORT_ATTEMPTS", "3")))


async def fetch_media_with_retry(url: str):
    """Retry a media download that died at the transport layer, and say so when it keeps dying.

    Measured on the local stack: 2 of 100 regression jobs settled as no_ready_candidates with the
    candidate error `{'type': 'ReadError', 'message': ''}` -- httpx raises its transport errors with
    an empty message, so the failure was recorded and still could not be read. A connection closed
    between requests (the serving side expiring a keep-alive connection) is transient for a
    download and must not cost a user their candidate permanently. Only httpx.TransportError is
    retried: a 404, a wrong content type, an oversized or undecodable body is a decision, not a
    race, and retrying those would only hide them slower.
    """
    last = None
    for attempt in range(MEDIA_TRANSPORT_ATTEMPTS):
        try:
            return await fetch_media(url)
        except httpx.TransportError as exc:
            last = exc
            if attempt + 1 < MEDIA_TRANSPORT_ATTEMPTS:
                await asyncio.sleep(0.5 * (2 ** attempt))
    cause = getattr(last, "__cause__", None) or (last.args[0] if last and last.args else "")
    raise RuntimeError(
        f"media download did not complete after {MEDIA_TRANSPORT_ATTEMPTS} transport attempts: "
        f"{type(last).__name__}{f' ({cause})' if cause else ' (no underlying error reported)'}"
    ) from last


async def store_media(job,ordinal,tmp_path,sha,mime,size,duration_ms):
    ext=mimetypes.guess_extension(mime) or Path(tmp_path).suffix or ".bin"
    key=f"{job['workspace_id']}/audio/{sha[:2]}/{sha}{ext}"
    with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM media_assets WHERE workspace_id=%s AND sha256=%s AND kind='audio'",(job["workspace_id"],sha))
        existing=cur.fetchone()
        if existing:
            return existing
    await asyncio.to_thread(
        s3.upload_file,tmp_path,S3_BUCKET,key,
        ExtraArgs={"ContentType":mime,"Metadata":{"sha256":sha,"scan-status":"clean"}},
    )
    with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("INSERT INTO media_assets(workspace_id,kind,bucket,object_key,sha256,mime_type,bytes,duration_ms,scan_status) VALUES(%s,'audio',%s,%s,%s,%s,%s,%s,'clean') ON CONFLICT(workspace_id,sha256,kind) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING *",(job["workspace_id"],S3_BUCKET,key,sha,mime,size,duration_ms))
        row=cur.fetchone();conn.commit();return row


async def cancel_if_requested(job,job_adapter):
    job_id=str(job["id"])
    with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status,provider_job_id,lease_owner,hold_id,workspace_id,attempt_count FROM generation_jobs WHERE id=%s",(job_id,));live=cur.fetchone()
    if not live or live["status"]!="cancel_requested":return False
    if live["lease_owner"] not in {None,WORKER_ID}:return True
    if live["provider_job_id"]:
        try:await job_adapter.cancel(live["provider_job_id"])
        except Exception as exc:print(f"provider cancel {job_id}: {exc}",flush=True)
    with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM generation_jobs WHERE id=%s FOR UPDATE",(job_id,));current=cur.fetchone()
        if not current or current["status"]=="cancelled":conn.commit();return True
        if current["status"]!="cancel_requested":conn.commit();return False
        if current["lease_owner"] not in {None,WORKER_ID}:conn.commit();return True
        settled,released,_=close_hold(cur,str(current["workspace_id"]),str(current["hold_id"]),0,job_id)
        cur.execute("UPDATE generation_jobs SET status='cancelled',completed_at=now(),lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=now(),settled_credits=%s WHERE id=%s",(settled,job_id))
        cur.execute("UPDATE generation_attempts SET outcome='cancelled',ended_at=now(),payload=%s WHERE job_id=%s AND attempt=%s",(psycopg2.extras.Json({"released":released}),job_id,current["attempt_count"]))
        emit(cur,current["workspace_id"],"GenerationCancelled",job_id,{"released":released});conn.commit()
    return True

async def process(job):
    job_id=str(job["id"]);stop=asyncio.Event();hb=asyncio.create_task(heartbeat(job_id,stop))
    result=None
    try:
        job=load_job(job_id)
        job_adapter,provider_name=create_adapter(job["provider"])
        if job["status"]=="cancelled":return
        if await cancel_if_requested(job,job_adapter):return
        if not job["provider_job_id"]:
            set_job(job_id,"submitting");step(job,"provider_submit",f"submit:{job_id}")
            provider_job_id=await job_adapter.submit(job_id,job["spec"],job["requested_candidates"],job["scenario"])
            with connect() as conn,conn.cursor() as cur:
                cur.execute("UPDATE generation_jobs SET provider_job_id=%s,provider_status='queued',status=CASE WHEN status='cancel_requested' THEN 'cancel_requested' ELSE 'provider_queued' END WHERE id=%s AND lease_owner=%s RETURNING status",(provider_job_id,job_id,WORKER_ID));persisted=cur.fetchone()
                if not persisted:
                    conn.rollback(); raise Retryable("lease lost while persisting provider job")
                emit(cur,job["workspace_id"],"ProviderJobSubmitted",job_id,{"provider_job_id":provider_job_id});conn.commit()
            step(job,"provider_submit",f"submit:{job_id}","completed",{"provider_job_id":provider_job_id})
            if persisted[0]=="cancel_requested":
                job=load_job(job_id)
                if await cancel_if_requested(job,job_adapter):return
            job=load_job(job_id)
        deadline=time.monotonic()+POLL_WINDOW
        poll_delay=PROVIDER_POLL_INTERVAL
        while time.monotonic()<deadline:
            if stop.is_set():raise Retryable("lease lost")
            if await cancel_if_requested(job,job_adapter):return
            result=await job_adapter.get_status(job["provider_job_id"]);set_job(job_id,"processing",provider_status=result.status)
            if result.status in {"completed","partial","failed"}:break
            await asyncio.sleep(poll_delay)
            poll_delay=min(PROVIDER_POLL_MAX_INTERVAL,poll_delay*1.35)
        else:raise Retryable("provider still processing after poll window")
        set_job(job_id,"ingesting",provider_status=result.status);step(job,"media_ingest",f"ingest:{job_id}")
        ready=0;failed=0;candidate_errors=[]
        for ordinal,candidate in enumerate(result.candidates[:int(job["requested_candidates"])],1):
            with connect() as conn,conn.cursor() as cur:
                cur.execute("SELECT status,lease_owner FROM generation_jobs WHERE id=%s",(job_id,));live=cur.fetchone()
            if not live or live[0]=="cancel_requested" or live[0]=="cancelled" or live[1]!=WORKER_ID: raise Retryable("job no longer active for this lease")
            clip_id=candidate.get("id") or f"{job['provider_job_id']}-{ordinal}"
            with connect() as conn,conn.cursor() as cur:
                cur.execute("SELECT status,metadata->>'ingest_error' FROM audio_candidates WHERE job_id=%s AND ordinal=%s",(job_id,ordinal));existing=cur.fetchone()
            if existing:
                ready+=1 if existing[0]=="ready" else 0;failed+=1 if existing[0]=="failed" else 0
                if existing[0]=="failed":candidate_errors.append({"ordinal":ordinal,"note":"candidate already recorded failed","ingest_error":json.loads(existing[1]) if existing[1] else None})
                continue
            if candidate.get("audio_url") and str(candidate.get("status","completed")).lower() not in {"failed","error"}:
                tmp=None
                try:
                    tmp,sha,mime,size,duration=await fetch_media_with_retry(candidate["audio_url"]);media=await store_media(job,ordinal,tmp,sha,mime,size,duration)
                    recipe={"song_spec_revision":job["spec_revision"],"provider":provider_name,"adapter_version":PROVIDER_ADAPTER_VERSION,"provider_job_id":job["provider_job_id"],"provider_clip_id":clip_id,"styles":job["spec"].get("styles"),"lyrics_sha256":hashlib.sha256(str(job["spec"].get("lyrics","")).encode()).hexdigest(),"audio_sha256":sha}
                    with connect() as conn,conn.cursor() as cur:
                        cur.execute("INSERT INTO audio_candidates(workspace_id,job_id,ordinal,provider_clip_id,status,media_asset_id,recipe,metadata) VALUES(%s,%s,%s,%s,'ready',%s,%s,%s) ON CONFLICT(job_id,ordinal) DO NOTHING",(job["workspace_id"],job_id,ordinal,clip_id,media["id"],psycopg2.extras.Json(recipe),psycopg2.extras.Json(candidate)));conn.commit();ready+=1
                except Exception as exc:
                    with connect() as conn,conn.cursor() as cur:
                        cur.execute("INSERT INTO audio_candidates(workspace_id,job_id,ordinal,provider_clip_id,status,recipe,metadata) VALUES(%s,%s,%s,%s,'failed',%s,%s) ON CONFLICT(job_id,ordinal) DO NOTHING",(job["workspace_id"],job_id,ordinal,clip_id,psycopg2.extras.Json({"provider":provider_name}),psycopg2.extras.Json({**candidate,"ingest_error":error_signature(exc)})));conn.commit();failed+=1;candidate_errors.append({"ordinal":ordinal,**error_signature(exc)})
                finally:
                    if tmp:Path(tmp).unlink(missing_ok=True)
            else:
                with connect() as conn,conn.cursor() as cur:
                    cur.execute("INSERT INTO audio_candidates(workspace_id,job_id,ordinal,provider_clip_id,status,recipe,metadata) VALUES(%s,%s,%s,%s,'failed',%s,%s) ON CONFLICT(job_id,ordinal) DO NOTHING",(job["workspace_id"],job_id,ordinal,clip_id,psycopg2.extras.Json({"provider":provider_name}),psycopg2.extras.Json(candidate)));conn.commit();failed+=1;candidate_errors.append({"ordinal":ordinal,"code":"provider_candidate_failed","provider_status":candidate.get("status") or "no_audio_url"})
        # Missing provider candidates are explicit failures.
        for ordinal in range(len(result.candidates)+1,int(job["requested_candidates"])+1):
            with connect() as conn,conn.cursor() as cur:
                cur.execute("INSERT INTO audio_candidates(workspace_id,job_id,ordinal,provider_clip_id,status,recipe,metadata) VALUES(%s,%s,%s,%s,'failed',%s,%s) ON CONFLICT(job_id,ordinal) DO NOTHING",(job["workspace_id"],job_id,ordinal,f"missing-{ordinal}",psycopg2.extras.Json({"provider":provider_name}),psycopg2.extras.Json({"error":{"code":"missing_provider_candidate"}})));conn.commit();failed+=1;candidate_errors.append({"ordinal":ordinal,"code":"missing_provider_candidate"})
        step(job,"media_ingest",f"ingest:{job_id}","completed",{"ready":ready,"failed":failed})
        settle=ready*int(job["unit_credits"]);final="completed" if ready==job["requested_candidates"] else "partial" if ready else "failed"
        job_error=terminal_error(final,result.raw.get("error"),ready,failed,int(job["requested_candidates"]),candidate_errors)
        with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM generation_jobs WHERE id=%s FOR UPDATE",(job_id,));current=cur.fetchone()
            if current["status"]=="cancelled":conn.commit();return
            if current["lease_owner"]!=WORKER_ID: raise Retryable("lease ownership changed before settlement")
            settled,released,hold_status=close_hold(cur,str(job["workspace_id"]),str(job["hold_id"]),settle,job_id)
            cost=job_adapter.reconcile_cost(result,ready)
            cur.execute("UPDATE generation_jobs SET status=%s,settled_credits=%s,actual_provider_cost=%s,completed_at=now(),lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=now(),error=%s WHERE id=%s",(final,settled,cost,psycopg2.extras.Json(job_error) if job_error else None,job_id))
            cur.execute("UPDATE generation_attempts SET outcome=%s,ended_at=now(),payload=%s WHERE job_id=%s AND attempt=%s",(final,psycopg2.extras.Json({"ready":ready,"failed":failed,"released":released}),job_id,job["attempt_count"]));emit(cur,job["workspace_id"],"GenerationSettled" if ready else "GenerationFailed",job_id,{"status":final,"ready":ready,"settled":settled,"released":released,"hold_status":hold_status});conn.commit()
            metric_inc("ai_music_worker_jobs_terminal_total",status=final);metric_inc("ai_music_worker_candidates_total",ready, status="ready");metric_inc("ai_music_worker_candidates_total",failed, status="failed")
    except Exception as exc:
        retryable=isinstance(exc,(Retryable,httpx.TimeoutException,httpx.TransportError)) or (isinstance(exc,httpx.HTTPStatusError) and (exc.response.status_code==429 or exc.response.status_code>=500))
        with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM generation_jobs WHERE id=%s FOR UPDATE",(job_id,));current=cur.fetchone()
            if not current or current["status"] in {"cancel_requested","cancelled"}:conn.commit();return
            if current["lease_owner"] not in {None,WORKER_ID}: conn.commit(); return
            if retryable and current["attempt_count"]<current["max_attempts"]:
                delay=min(120,2**current["attempt_count"]*3);metric_inc("ai_music_worker_retries_total",error=type(exc).__name__);cur.execute("UPDATE generation_jobs SET status='retry_wait',next_attempt_at=now()+(%s||' seconds')::interval,lease_owner=NULL,lease_expires_at=NULL,error=%s WHERE id=%s",(delay,psycopg2.extras.Json({"type":type(exc).__name__,"message":str(exc),"retryable":True}),job_id));outcome="retry_wait"
            else:
                settled,released,_=close_hold(cur,str(current["workspace_id"]),str(current["hold_id"]),0,job_id);metric_inc("ai_music_worker_jobs_terminal_total",status="dead_letter");cur.execute("UPDATE generation_jobs SET status='dead_letter',completed_at=now(),lease_owner=NULL,lease_expires_at=NULL,error=%s WHERE id=%s",(psycopg2.extras.Json({"type":type(exc).__name__,"message":str(exc),"retryable":retryable}),job_id));emit(cur,current["workspace_id"],"GenerationDeadLettered",job_id,{"released":released,"error":str(exc)});outcome="dead_letter"
            cur.execute("UPDATE generation_attempts SET outcome=%s,error_class=%s,ended_at=now(),payload=%s WHERE job_id=%s AND attempt=%s",(outcome,type(exc).__name__,psycopg2.extras.Json({"message":str(exc)}),job_id,current["attempt_count"]));conn.commit()
        print(f"job {job_id} {outcome}: {exc}",flush=True)
    finally:
        stop.set();hb.cancel()
        try:await hb
        except BaseException:pass

async def publish_outbox():
    while True:
        try:
            with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM domain_outbox WHERE published_at IS NULL ORDER BY occurred_at FOR UPDATE SKIP LOCKED LIMIT 50");rows=cur.fetchall()
                for row in rows:
                    try:
                        rq.xadd("domain_events",{"event_id":str(row["id"]),"event_type":row["event_type"],"aggregate_id":row["aggregate_id"],"workspace_id":str(row["workspace_id"] or ""),"payload":json.dumps(row["payload"],ensure_ascii=False)},maxlen=10000,approximate=True)
                        cur.execute("UPDATE domain_outbox SET published_at=now(),publish_attempts=publish_attempts+1 WHERE id=%s",(row["id"],));metric_inc("ai_music_worker_outbox_published_total")
                    except Exception as exc:metric_inc("ai_music_worker_outbox_errors_total",error=type(exc).__name__);cur.execute("UPDATE domain_outbox SET publish_attempts=publish_attempts+1,last_error=%s WHERE id=%s",(str(exc),row["id"]))
                conn.commit()
        except Exception as exc:print(f"outbox: {exc}",flush=True)
        await asyncio.sleep(1)

async def consume_inbox():
    while True:
        try:
            with connect() as conn,conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM provider_inbox WHERE processed_at IS NULL ORDER BY received_at FOR UPDATE SKIP LOCKED LIMIT 20");rows=cur.fetchall()
                for row in rows:
                    provider_job_id=row["payload"].get("provider_job_id") or row["payload"].get("job_id")
                    if provider_job_id:cur.execute("UPDATE generation_jobs SET provider_status=COALESCE(%s,provider_status) WHERE provider_job_id=%s",(row["payload"].get("status"),provider_job_id))
                    cur.execute("UPDATE provider_inbox SET processed_at=now() WHERE id=%s",(row["id"],))
                conn.commit()
        except Exception as exc:print(f"inbox: {exc}",flush=True)
        await asyncio.sleep(2)


def sample_queue_metrics_once():
    with connect() as conn,conn.cursor() as cur:
        cur.execute("""SELECT
          count(*) FILTER (WHERE status IN ('queued','retry_wait','leased','submitting','provider_queued','processing','ingesting','cancel_requested')) AS active,
          count(*) FILTER (WHERE status IN ('queued','retry_wait')) AS waiting,
          COALESCE(EXTRACT(EPOCH FROM (now()-min(created_at) FILTER (WHERE status IN ('queued','retry_wait')))),0) AS oldest_wait_seconds
          FROM generation_jobs""")
        active,waiting,oldest=cur.fetchone()
    set_gauge("ai_music_worker_jobs_active",float(active or 0))
    set_gauge("ai_music_worker_jobs_waiting",float(waiting or 0))
    set_gauge("ai_music_worker_oldest_wait_seconds",float(oldest or 0))

async def sample_queue_metrics():
    while True:
        try:
            await asyncio.to_thread(sample_queue_metrics_once)
        except Exception as exc:
            print(f"queue metrics: {exc}",flush=True)
        await asyncio.sleep(2)

async def job_loop(slot:int):
    set_gauge("ai_music_worker_slot_busy",0,slot=str(slot))
    while True:
        job=await asyncio.to_thread(claim_job)
        if not job:
            await asyncio.sleep(CLAIM_IDLE_SLEEP)
            continue
        set_gauge("ai_music_worker_slot_busy",1,slot=str(slot))
        try:
            await process(job)
        except Exception as exc:
            # process() handles domain errors itself, but an unexpected exception
            # must not take down the whole worker loop (and thus every queued job).
            print(f"worker slot {slot}: job {job.get('id')} crashed: {exc}",flush=True)
        finally:
            set_gauge("ai_music_worker_slot_busy",0,slot=str(slot))

async def main():
    start_metrics_server(int(os.getenv("METRICS_PORT","9101")))
    set_gauge("ai_music_worker_build_info",1,version="14.0.0")
    set_gauge("ai_music_worker_concurrency",WORKER_CONCURRENCY)
    ensure_bucket();db_pool()
    print(f"worker {WORKER_ID} ready default_provider={default_provider_name} concurrency={WORKER_CONCURRENCY}",flush=True)
    bg=[asyncio.create_task(publish_outbox()),asyncio.create_task(consume_inbox()),asyncio.create_task(sample_queue_metrics())]
    slots=[asyncio.create_task(job_loop(i)) for i in range(WORKER_CONCURRENCY)]
    try:
        await asyncio.gather(*slots)
    finally:
        for task in slots+bg:task.cancel()
        await asyncio.gather(*(slots+bg),return_exceptions=True)
        await close_provider_http_client()
        await close_media_http_client()
        close_db_pool()

if __name__=="__main__":asyncio.run(main())
