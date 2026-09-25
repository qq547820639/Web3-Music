import hashlib,hmac,io,json,os,time,uuid,zipfile
import requests

ROOT=os.getenv("API_BASE_URL","http://localhost:8000");BASE=ROOT+"/api"
OWNER=("owner@example.local","demo-owner");CREATOR=("creator@example.local","demo-creator");OTHER=("viewer@other.local","demo-viewer")

def login(creds):
    r=requests.post(BASE+"/auth/login",json={"email":creds[0],"password":creds[1]},timeout=20);assert r.status_code==200,r.text;d=r.json();return d["access_token"],d["workspaces"][0]["id"]
OWNER_TOKEN,OWNER_WS=login(OWNER);CREATOR_TOKEN,CREATOR_WS=login(CREATOR);OTHER_TOKEN,OTHER_WS=login(OTHER)
def headers(token=OWNER_TOKEN,ws=OWNER_WS,extra=None):return {"Authorization":"Bearer "+token,"X-Workspace-Id":ws,"Content-Type":"application/json",**(extra or {})}
def call(method,path,token=OWNER_TOKEN,ws=OWNER_WS,ok=range(200,400),**kwargs):
    r=requests.request(method,BASE+path,headers={**headers(token,ws),**kwargs.pop("headers",{})},timeout=30,**kwargs);assert r.status_code in ok,f"{method} {path}: {r.status_code} {r.text}";return r
def wait_job(job_id,timeout=70):
    end=time.time()+timeout
    while time.time()<end:
        d=call("GET",f"/jobs/{job_id}").json()
        if d["job"]["status"] in {"completed","partial","failed","dead_letter","cancelled"}:return d
        time.sleep(1)
    raise AssertionError("job did not finish")

def make_project(title="Acceptance"):
    return call("POST","/projects",json={"title":title+" "+uuid.uuid4().hex[:6]}).json()



def test_frontend_and_observability_smoke():
    web=os.getenv("WEB_BASE_URL","http://web")
    admin=os.getenv("ADMIN_BASE_URL","http://admin")
    worker_metrics=os.getenv("WORKER_METRICS_BASE_URL","http://worker:9101")
    web_page=requests.get(web+"/",timeout=20)
    admin_page=requests.get(admin+"/",timeout=20)
    api_metrics=requests.get(ROOT+"/metrics",timeout=20)
    worker=requests.get(worker_metrics+"/metrics",timeout=20)
    assert web_page.status_code==200 and "Resonance" in web_page.text
    assert admin_page.status_code==200 and "Resonance Control Plane" in admin_page.text
    assert api_metrics.status_code==200 and "ai_music_api_info" in api_metrics.text
    assert worker.status_code==200 and "ai_music_worker_up" in worker.text


def test_auth_tenant_lock_quality_generation_asset_and_refund():
    before=call("GET","/ledger").json()["balances"]
    created=make_project();pid=created["id"];original=created["spec"]["lyrics"]
    # Cross-tenant access is hidden by membership + RLS.
    cross=requests.get(BASE+f"/projects/{pid}",headers=headers(OTHER_TOKEN,OTHER_WS),timeout=20);assert cross.status_code==404
    call("PUT",f"/projects/{pid}/locks",json={"locked_paths":["/lyrics"]})
    blocked=requests.post(BASE+f"/projects/{pid}/patch",headers=headers(),json={"base_revision":1,"operations":[{"op":"replace","path":"/lyrics","value":"forbidden"}],"reason":"negative lock test"},timeout=20);assert blocked.status_code==409
    chat=call("POST",f"/projects/{pid}/chat",json={"message":"保留歌词，编曲更克制","base_revision":1,"apply":True}).json();assert chat["applied"]["revision"]==2;assert chat["applied"]["spec"]["lyrics"]==original
    q1=call("POST",f"/projects/{pid}/quality?revision=2").json();q2=call("POST",f"/projects/{pid}/quality?revision=2").json();assert q1["score"]==q2["score"] and len(q1["dimensions"])==28
    quote=call("POST",f"/projects/{pid}/quotes",json={"spec_revision":2,"candidate_count":2,"scenario":"success"}).json();key="acc-"+uuid.uuid4().hex;body={"quote_id":quote["id"],"quote_hash":quote["quote_hash"],"user_confirmation":True}
    j1=call("POST","/jobs",headers={"Idempotency-Key":key},json=body).json();j2=call("POST","/jobs",headers={"Idempotency-Key":key},json=body).json();assert j1["id"]==j2["id"]
    conflict=requests.post(BASE+"/jobs",headers=headers(extra={"Idempotency-Key":key}),json={**body,"quote_hash":"0"*64},timeout=20);assert conflict.status_code==409
    done=wait_job(j1["id"]);assert done["job"]["status"]=="completed";ready=[c for c in done["candidates"] if c["status"]=="ready"];assert len(ready)==2 and all(len(c["sha256"])==64 for c in ready)
    token=call("POST",f"/candidates/{ready[0]['id']}/media-token").json();audio=requests.get(ROOT+token["url"],timeout=20);assert audio.status_code==200 and len(audio.content)>1000
    asset=call("POST",f"/projects/{pid}/master",json={"candidate_id":ready[1]["id"],"expected_spec_revision":2,"confirmation":True}).json();detail=call("GET",f"/assets/{asset['asset_snapshot_id']}").json();manifest=detail["rights"]["manifest"];assert manifest["capabilities"]["download"]["status"]=="allowed" and manifest["capabilities"]["commercial_use"]["status"]=="blocked"
    exported=call("GET",f"/assets/{asset['asset_snapshot_id']}/export");z=zipfile.ZipFile(io.BytesIO(exported.content));assert {"metadata/asset-snapshot.json","metadata/rights-manifest.json","metadata/recipe.json"}.issubset(set(z.namelist()))
    failq=call("POST",f"/projects/{pid}/quotes",json={"spec_revision":2,"candidate_count":1,"scenario":"failed"}).json();fail=call("POST","/jobs",headers={"Idempotency-Key":"fail-"+uuid.uuid4().hex},json={"quote_id":failq["id"],"quote_hash":failq["quote_hash"],"user_confirmation":True}).json();failed=wait_job(fail["id"]);assert failed["job"]["status"]=="failed" and int(failed["job"]["settled_credits"])==0
    after=call("GET","/ledger").json()["balances"];assert float(after["held"])==float(before["held"]);assert float(after["available"])==float(before["available"])-20;assert float(after["expense"])==float(before["expense"])+20

def test_partial_success_isolated_hold_and_admin_policy():
    before=call("GET","/ledger").json()["balances"];p=make_project("Partial");q=call("POST",f"/projects/{p['id']}/quotes",json={"candidate_count":2,"scenario":"partial_success"}).json();job=call("POST","/jobs",headers={"Idempotency-Key":"partial-"+uuid.uuid4().hex},json={"quote_id":q["id"],"quote_hash":q["quote_hash"],"user_confirmation":True}).json();done=wait_job(job["id"]);assert done["job"]["status"]=="partial";assert int(done["job"]["settled_credits"])==10;assert float(done["job"]["released_amount"])==10
    after=call("GET","/ledger").json()["balances"];assert float(after["available"])==float(before["available"])-10 and float(after["expense"])==float(before["expense"])+10 and float(after["held"])==float(before["held"])
    dashboard=call("GET","/admin/dashboard").json();assert dashboard["counts"]["jobs"]>=1
    own_admin=requests.get(BASE+"/admin/dashboard",headers=headers(OTHER_TOKEN,OTHER_WS),timeout=20);assert own_admin.status_code==200
    creator_forbidden=requests.get(BASE+"/admin/dashboard",headers=headers(CREATOR_TOKEN,CREATOR_WS),timeout=20);assert creator_forbidden.status_code==403

def test_webhook_signature_and_inbox_deduplication():
    payload={"type":"job.status","provider_job_id":"unknown","status":"processing"};raw=json.dumps(payload,separators=(",",":")).encode();secret=os.getenv("PROVIDER_WEBHOOK_SECRET","dev-provider-webhook-secret");sig=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest();event="evt-"+uuid.uuid4().hex
    h={"Content-Type":"application/json","X-Signature":sig,"X-Event-Id":event}
    a=requests.post(BASE+"/provider-webhooks/emulator",data=raw,headers=h,timeout=20);b=requests.post(BASE+"/provider-webhooks/emulator",data=raw,headers=h,timeout=20);assert a.status_code==200 and b.status_code==200;assert a.json()["accepted"] is True and b.json()["duplicate"] is True
    bad=requests.post(BASE+"/provider-webhooks/emulator",data=raw,headers={**h,"X-Signature":"bad"},timeout=20);assert bad.status_code==401

def test_cancellation_releases_job_hold_without_settlement():
    before=call("GET","/ledger").json()["balances"]
    p=make_project("Cancel")
    q=call("POST",f"/projects/{p['id']}/quotes",json={"candidate_count":1,"scenario":"timeout"}).json()
    job=call("POST","/jobs",headers={"Idempotency-Key":"cancel-"+uuid.uuid4().hex},json={"quote_id":q["id"],"quote_hash":q["quote_hash"],"user_confirmation":True}).json()
    time.sleep(1)
    requested=call("POST",f"/admin/jobs/{job['id']}/cancel").json();assert requested["status"]=="cancel_requested"
    done=wait_job(job["id"],timeout=35);assert done["job"]["status"]=="cancelled";assert float(done["job"]["released_amount"])==10
    after=call("GET","/ledger").json()["balances"];assert float(after["available"])==float(before["available"]);assert float(after["held"])==float(before["held"]);assert float(after["expense"])==float(before["expense"])


def test_platform_switch_requires_platform_admin():
    other=requests.put(BASE+"/admin/switches/generation_enabled",headers=headers(OTHER_TOKEN,OTHER_WS),json={"value":True},timeout=20);assert other.status_code==403
    creator=requests.put(BASE+"/admin/switches/generation_enabled",headers=headers(CREATOR_TOKEN,CREATOR_WS),json={"value":True},timeout=20);assert creator.status_code==403
    owner=call("PUT","/admin/switches/generation_enabled",json={"value":True});assert owner.json()["value"] is True

def test_provider_emulator_contract():
    provider=os.getenv("PROVIDER_BASE_URL","http://provider-emulator:8010")
    cap=requests.get(provider+"/v1/capabilities",timeout=20);assert cap.status_code==200;assert cap.json()["supports_cancel"] is True
    key="provider-contract-"+uuid.uuid4().hex
    body={"title":"Contract","lyrics":"line one line two","styles":"piano pop","bpm":80,"candidate_count":1,"scenario":"success"}
    a=requests.post(provider+"/v1/jobs",headers={"Idempotency-Key":key},json=body,timeout=20);b=requests.post(provider+"/v1/jobs",headers={"Idempotency-Key":key},json=body,timeout=20);assert a.status_code==202 and b.status_code==202 and a.json()["id"]==b.json()["id"]
    conflict=requests.post(provider+"/v1/jobs",headers={"Idempotency-Key":key},json={**body,"title":"different"},timeout=20);assert conflict.status_code==409

def wait_order(order_id, wanted, timeout=30):
    end=time.time()+timeout
    while time.time()<end:
        rows=call("GET","/orders").json()["items"]
        row=next(x for x in rows if x["id"]==order_id)
        if row["status"] in wanted:return row
        time.sleep(.5)
    raise AssertionError(f"order {order_id} did not reach {wanted}")


def test_v12_creation_collaboration_preferences_and_support():
    p=make_project("Collaboration")
    branch=call("POST",f"/projects/{p['id']}/branches",json={"name":"alternate","base_revision":1}).json();assert branch["head_revision"]==1
    comment=call("POST",f"/projects/{p['id']}/comments",json={"body":"副歌需要更多留白","spec_revision":1,"timecode_ms":1200}).json();assert comment["status"]=="open"
    resolved=call("POST",f"/projects/{p['id']}/comments/{comment['id']}/resolve",json={}).json();assert resolved["status"]=="resolved"
    pref=call("PUT","/preferences",json={"scope":"workspace","preferences":{"preferred_bpm":82,"vocal":"restrained"},"learning_enabled":True}).json();assert pref["preferences"]["preferred_bpm"]==82
    ticket=call("POST","/support/tickets",json={"category":"other","priority":"normal","subject":"Acceptance support case","description":"Verifies the support workflow."}).json();assert ticket["status"]=="open"


def test_v12_credit_payment_refund_and_payment_idempotency():
    before=float(call("GET","/ledger").json()["balances"]["available"])
    order=call("POST","/orders/credits",json={"sku":"CREDITS_100","quantity":1}).json()
    key="payment-"+uuid.uuid4().hex
    started=call("POST",f"/orders/{order['id']}/pay",headers={"Idempotency-Key":key},json={"scenario":"success"}).json()
    assert started["order_id"]==order["id"]
    fulfilled=wait_order(order["id"],{"fulfilled"});assert fulfilled["status"]=="fulfilled"
    # The list endpoint once aliased its window count as "total" and clobbered orders.total,
    # so the web UI rendered the page row count as the amount paid (app.js:1501).
    assert str(fulfilled["total"])==str(order["total"]),f"orders list changed the money total: {fulfilled['total']} != {order['total']}"
    assert int(fulfilled["total"])==sum(int(line["total"]) for line in fulfilled["items"]),"order total does not equal the sum of its line items"
    retry=call("POST",f"/orders/{order['id']}/pay",headers={"Idempotency-Key":key},json={"scenario":"success"}).json();assert retry["id"]==started["id"]
    conflict=requests.post(BASE+f"/orders/{order['id']}/pay",headers=headers(extra={"Idempotency-Key":key}),json={"scenario":"failed"},timeout=20);assert conflict.status_code==409
    after_purchase=float(call("GET","/ledger").json()["balances"]["available"]);assert after_purchase==before+100
    refund=call("POST",f"/orders/{order['id']}/refunds",headers={"Idempotency-Key":"refund-"+uuid.uuid4().hex},json={"reason":"Acceptance full refund"}).json();assert refund["status"]=="processing"
    wait_order(order["id"],{"refunded"})
    after_refund=float(call("GET","/ledger").json()["balances"]["available"]);assert after_refund==before


def test_v12_default_provider_cannot_create_commercial_offer():
    p=make_project("Rights Block")
    q=call("POST",f"/projects/{p['id']}/quotes",json={"candidate_count":1,"scenario":"success"}).json()
    job=call("POST","/jobs",headers={"Idempotency-Key":"rights-"+uuid.uuid4().hex},json={"quote_id":q["id"],"quote_hash":q["quote_hash"],"user_confirmation":True}).json()
    done=wait_job(job["id"]);candidate=next(c for c in done["candidates"] if c["status"]=="ready")
    asset=call("POST",f"/projects/{p['id']}/master",json={"candidate_id":candidate["id"],"expected_spec_revision":1,"confirmation":True}).json()
    blocked=requests.post(BASE+"/offers",headers=headers(),json={"asset_snapshot_id":asset["asset_snapshot_id"],"title":"Must remain blocked","price_amount":100,"currency":"USD","status":"active"},timeout=20)
    assert blocked.status_code==409


def test_payment_emulator_contract():
    provider=os.getenv("PAYMENT_BASE_URL","http://payment-emulator:8020")
    key="pay-contract-"+uuid.uuid4().hex
    body={"amount":1200,"currency":"USD","scenario":"processing","callback_url":"http://api:8000/api/payment-webhooks/emulator","metadata":{"payment_id":str(uuid.uuid4()),"order_id":str(uuid.uuid4()),"workspace_id":OWNER_WS}}
    a=requests.post(provider+"/v1/intents",headers={"Idempotency-Key":key},json=body,timeout=20);b=requests.post(provider+"/v1/intents",headers={"Idempotency-Key":key},json=body,timeout=20)
    assert a.status_code==200 and b.status_code==200 and a.json()["id"]==b.json()["id"]
    different=requests.post(provider+"/v1/intents",headers={"Idempotency-Key":key},json={**body,"amount":1300},timeout=20);assert different.status_code==409
