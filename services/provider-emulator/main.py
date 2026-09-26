import hashlib,json,math,os,random,sqlite3,struct,threading,time,uuid,wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import Body,FastAPI,Header,HTTPException,Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,ValidationError

app=FastAPI(title="Persistent Music Provider Emulator",version="2.1.0")
MEDIA_ROOT=Path(os.getenv("MEDIA_ROOT","/data/provider-media"));MEDIA_ROOT.mkdir(parents=True,exist_ok=True)
DB_PATH=os.getenv("SQLITE_PATH","/data/provider/provider.db");Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
app.mount("/media",StaticFiles(directory=MEDIA_ROOT),name="media")

class JobRequest(BaseModel):
    title:str;lyrics:str;styles:str;bpm:int=90;candidate_count:int=Field(default=2,ge=1,le=8);scenario:str="success"

class SongSpec(BaseModel):
    # The vendor contract (shared/contracts/provider-submit-v1.schema.json) nests the spec;
    # the emulator's own flat dialect does not, so both are modelled here.
    title:str=Field(min_length=1);lyrics:str="";styles:list[str]|str="";bpm:int=90

class VendorJobRequest(BaseModel):
    external_request_id:str=Field(min_length=1)
    model:str|None=None
    candidate_count:int=Field(default=2,ge=1,le=8)
    song_spec:SongSpec

def normalise_submit(raw:dict)->tuple[JobRequest,dict]:
    """Accept the vendor-facing contract as well as the emulator's own flat shape.

    Before this, only the flat shape existed, so GenericRESTAdapter -- the adapter the platform
    uses for any provider it did not write -- was rejected with 422 by this service, and the
    divergence was invisible because nothing wrote the contract down.
    """
    try:
        if "song_spec" in raw:
            vendor=VendorJobRequest(**raw)
            spec=vendor.song_spec
            styles=",".join(spec.styles) if isinstance(spec.styles,list) else spec.styles
            return (JobRequest(title=spec.title,lyrics=spec.lyrics,styles=styles,bpm=spec.bpm,
                               candidate_count=vendor.candidate_count,scenario="success"),
                    {"dialect":"provider-submit-v1","external_request_id":vendor.external_request_id,"model":vendor.model})
        return JobRequest(**raw),{"dialect":"emulator-native"}
    except ValidationError as exc:
        raise HTTPException(422,exc.errors()) from exc

def db():
    conn=sqlite3.connect(DB_PATH,timeout=10);conn.row_factory=sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,request_hash TEXT NOT NULL,request_json TEXT NOT NULL,base_url TEXT NOT NULL,created_at REAL NOT NULL,cancelled INTEGER NOT NULL DEFAULT 0,results_json TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS submit_attempts(idempotency_key TEXT PRIMARY KEY,attempts INTEGER NOT NULL DEFAULT 0)");conn.commit();return conn

def write_wav(path:Path,seed:str,bpm:int,ordinal:int):
    random.seed(seed+str(ordinal));rate=22050;duration=8;root=48+random.randint(0,8);scale=[0,3,5,7,10] if ordinal%2 else [0,2,4,7,9];frames=bytearray();beat=60/max(45,min(180,bpm))
    for i in range(rate*duration):
        t=i/rate;step=int(t/(beat/2))%len(scale);midi=root+scale[step]+(12 if step>=3 else 0);freq=440*(2**((midi-69)/12));chord=440*(2**(((root+(3 if ordinal%2 else 4))-69)/12));bass=440*(2**(((root-12)-69)/12));env=min(1,(t%(beat/2))/.03)*max(.15,1-(t%(beat/2))/(beat/2)*.75);value=.34*math.sin(2*math.pi*freq*t)*env+.18*math.sin(2*math.pi*chord*t)+.16*math.sin(2*math.pi*bass*t)
        if (t%beat)<.08:value+=.18*math.sin(2*math.pi*70*t)*(1-(t%beat)/.08)
        fade=min(1,t/.25,(duration-t)/.5);frames.extend(struct.pack("<h",int(max(-1,min(1,value*fade))*32767)))
    # Published by rename, not in place: `if not path.exists()` elsewhere treats an existing file
    # as complete, so a process killed mid-write would otherwise serve a torn WAV forever.
    tmp=Path(f"{path}.{threading.get_ident()}.part")
    with wave.open(str(tmp),"wb") as wf:wf.setnchannels(1);wf.setsampwidth(2);wf.setframerate(rate);wf.writeframes(frames)
    os.replace(tmp,path)

# Clip synthesis is pure-Python and costs ~0.75-1.0s per candidate measured in this container, so
# it cannot run where a client is waiting: create_job is `async def`, and generating inside it took
# a concurrent GET /health 9.95s (see docs/FINAL_RELEASE_STATUS.md). Two workers because the GIL
# serialises the maths anyway; the point is off the event loop, not parallelism.
_GENERATOR=ThreadPoolExecutor(max_workers=2,thread_name_prefix="clip")
_GENERATING=set();_GEN_LOCK=threading.Lock()

def build_results(row,req):
    """The expensive half: synthesise every candidate clip. Only ever called off the request path."""
    results=[];success=req["candidate_count"] if req["scenario"]!="partial_success" else max(1,req["candidate_count"]-1)
    for ordinal in range(req["candidate_count"]):
        clip=f"emu-{row['id'][:8]}-{ordinal+1}"
        if ordinal<success:
            filename=clip+".wav";path=MEDIA_ROOT/filename
            if not path.exists():write_wav(path,row["id"],req["bpm"],ordinal)
            results.append({"id":clip,"status":"completed","title":f"{req['title']} · Emulator {ordinal+1}","audio_url":row["base_url"].rstrip("/")+"/media/"+filename,"duration":8.0,"metadata":{"ordinal":ordinal+1,"provider":"emulator","not_suno":True}})
        else:results.append({"id":clip,"status":"failed","error":{"code":"candidate_failed","message":"Emulated partial failure"}})
    return results

def publish(job_id):
    """Generate this job's clips, then make them visible by writing results_json.

    Ordering is the contract: a caller that sees completed fetches audio_url immediately, so no
    result may be published before its bytes are on disk.
    """
    row=None
    try:
        with db() as conn:
            row=conn.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
        if not row or row["results_json"] or row["cancelled"]:return
        req=json.loads(row["request_json"])
        if req["scenario"] in {"timeout","failed"}:return
        results=build_results(row,req)
        with db() as conn:
            conn.execute("UPDATE jobs SET results_json=? WHERE id=? AND results_json IS NULL",(json.dumps(results,ensure_ascii=False),job_id));conn.commit()
    except Exception as exc:
        # A silent hang here is worse than a visible failure: a poller would wait it out and report
        # a timeout with no cause, which is how the platform's own retries are tuned.
        try:count=json.loads(row["request_json"])["candidate_count"] if row is not None else 1
        except Exception:count=1
        failed=[{"id":f"emu-{job_id[:8]}-{i+1}","status":"failed","error":{"code":"emulator_generation_error","message":str(exc)}} for i in range(count)]
        try:
            with db() as conn:
                conn.execute("UPDATE jobs SET results_json=? WHERE id=? AND results_json IS NULL",(json.dumps(failed),job_id));conn.commit()
        except Exception:pass
    finally:
        with _GEN_LOCK: _GENERATING.discard(job_id)

def generate(job_id):
    with _GEN_LOCK:
        if job_id in _GENERATING:return False
        _GENERATING.add(job_id)
    try:
        _GENERATOR.submit(publish,job_id);return True
    except RuntimeError:
        with _GEN_LOCK: _GENERATING.discard(job_id);return False

def resolve(row):
    """Read-only view of a job: no clip is ever synthesised while a client is waiting."""
    req=json.loads(row["request_json"]);elapsed=time.time()-row["created_at"]
    if row["cancelled"]:return {"id":row["id"],"status":"cancelled","results":[],"provider":"emulator"}
    if req["scenario"]=="timeout":return {"id":row["id"],"status":"processing","results":[],"provider":"emulator"}
    if elapsed<.7:return {"id":row["id"],"status":"queued","results":[],"provider":"emulator"}
    if elapsed<2:return {"id":row["id"],"status":"processing","results":[],"provider":"emulator"}
    if req["scenario"]=="failed":return {"id":row["id"],"status":"failed","results":[],"provider":"emulator","error":{"code":"provider_generation_error","message":"Emulated provider failure"}}
    if not row["results_json"]:
        generate(row["id"])
        return {"id":row["id"],"status":"processing","results":[],"provider":"emulator"}
    results=json.loads(row["results_json"])
    return {"id":row["id"],"status":"partial" if any(x["status"]=="failed" for x in results) else "completed","results":results,"provider":"emulator"}

@app.get("/health")
def health():return {"status":"ok","provider":"emulator","persistence":"sqlite","notice":"development only; not Suno"}
@app.get("/v1/capabilities")
def capabilities():return {"provider":"emulator","async":True,"candidate_count_max":8,"supports_custom_lyrics":True,"supports_styles":True,"supports_cancel":True,"supports_webhooks":False,"commercial_rights":"blocked","approval_status":"development_only"}

@app.post("/v1/jobs",status_code=202)
async def create_job(request:Request,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key")):
    if not idempotency_key:raise HTTPException(400,"Idempotency-Key required")
    body,provenance=normalise_submit(await request.json())
    if body.scenario not in {"success","partial_success","failed","timeout","rate_limited"}:raise HTTPException(400,"unknown scenario")
    payload=body.model_dump();payload.update(provenance);request_hash=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    with db() as conn:
        existing=conn.execute("SELECT * FROM jobs WHERE idempotency_key=?",(idempotency_key,)).fetchone()
        if existing:
            if existing["request_hash"]!=request_hash:raise HTTPException(409,"idempotency key reused with different request")
            return resolve(existing)
        if body.scenario=="rate_limited":
            row=conn.execute("SELECT attempts FROM submit_attempts WHERE idempotency_key=?",(idempotency_key,)).fetchone();attempts=(row[0] if row else 0)+1
            conn.execute("INSERT INTO submit_attempts(idempotency_key,attempts) VALUES(?,?) ON CONFLICT(idempotency_key) DO UPDATE SET attempts=excluded.attempts",(idempotency_key,attempts));conn.commit()
            if attempts<=2:raise HTTPException(429,"emulated rate limit")
            payload["scenario"]="success"
        job_id=str(uuid.uuid4());base=str(request.base_url).rstrip("/")
        conn.execute("INSERT INTO jobs(id,idempotency_key,request_hash,request_json,base_url,created_at) VALUES(?,?,?,?,?,?)",(job_id,idempotency_key,request_hash,json.dumps(payload,ensure_ascii=False),base,time.time()));conn.commit()
        # Queued behind the commit so the worker thread can see the row: a real provider starts
        # generating when it accepts the job, not when someone next happens to poll it.
        generate(job_id)
        row=conn.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone();return resolve(row)

@app.get("/v1/jobs/{job_id}")
def get_job(job_id:str):
    with db() as conn:row=conn.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
    if not row:raise HTTPException(404,"job not found")
    return resolve(row)

@app.post("/v1/jobs/{job_id}/cancel")
def cancel(job_id:str):
    with db() as conn:
        cur=conn.execute("UPDATE jobs SET cancelled=1 WHERE id=?",(job_id,));conn.commit()
    if cur.rowcount!=1:raise HTTPException(404,"job not found")
    return {"id":job_id,"status":"cancelled"}
