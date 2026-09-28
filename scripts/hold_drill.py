#!/usr/bin/env python3
"""Resident drill for what a Legal Hold actually holds, measured against the running stack.

`docs/RIGHTS_POLICY.md` promises that an accepted notice freezes deletion. A promise repeated across
rounds of records is not evidence, so this drill measures the flag's real reach: which doors refuse, in
which words, which doors do not, and -- the part that changed the writing of this file -- which clauses
in the SQL cannot be reached at all from the surface that writes them.

Two carriers hold the flag and they behave differently:
  * `rights_manifests.manifest->>'legal_hold'`, written only by `POST /assets/{id}/rights-review`
    (`services/api/app/routers/assets.py:174-177`), which blocks seven capabilities in the same
    statement;
  * `moderation_cases.legal_hold`, read together with the case's *status*, so a case whose flag is off
    while it is still `open` keeps excluding the offer -- one predicate, two halves, tested apart.

Refusals are certified on both polarities: held -> refused with the exact words, released -> allowed.
A refusal that fires because the fixture is broken is the false green this file exists to avoid.

The manifest carrier turns out to be shielded by ordering, and that is asserted rather than assumed:
`rights_manifests` refuses UPDATE (`immutable_rights_manifests`), the review door only ever *adds a
version*, and both `reserve_marketplace_offer` and `prepare_brand_award` test "is my pinned manifest
still the newest" **before** they test the hold. So a hold arriving after an offer or a submission is
answered as `... manifest is stale`, and the flag clause behind it has no producer that can reach it --
no offer in this database is pinned to a held manifest, and creating one while held is refused. That is
defence in depth, not a live path, and the drill says so in the check names.

Fixtures reuse the deployment's demo seller/buyer but register every id they create, and teardown
removes the market and moderation layer by those ids with `session_replication_role = replica` written
into each statement -- announced rather than hidden, because the same session is where the immutable
tables were just shown to refuse deletion. What teardown deliberately does NOT remove is the financial
trail (order, licence, split, ledger): deleting behind a settled ledger would move the absolute balances
`restore_fidelity.py` fingerprints, so the residue is asserted whole instead of erased.

Run against the stack with the commercial overlay up (`-f docker-compose.commercial-test.yml`), which is
what chain step `commercial-flow` leaves behind: the marketplace doors need approved commercial rights.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_client  # noqa: E402  -- the chain's own credit precondition lives here, not in a copy
import metric_line as metrics  # noqa: E402

BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000") + "/api"
STAMP = time.strftime("%m%dT%H%M%SZ", time.gmtime())
SELLER = (os.getenv("E2E_EMAIL", "owner@example.local"), os.getenv("E2E_PASSWORD", "demo-owner"))
BUYER = ("viewer@other.local", "demo-viewer")

checks: list[tuple[str, bool, str]] = []

# Every row this run creates is registered by id, because the tables that hold them are joined by
# project and by snapshot, and a teardown that pattern-matches on a column it guessed (`review_note`
# is not a column of rights_manifests) fails quietly and leaves fixtures in the shared demo workspace.
MADE: dict[str, list[str]] = {k: [] for k in ("projects", "snapshots", "manifests", "evidence",
                                              "offers", "cases", "briefs", "submissions", "orders",
                                              "candidates", "media")}


def keep(kind: str, ident):
    if ident:
        MADE[kind].append(str(ident))
    return ident


def ok2(rc) -> bool:
    """2xx with a body back. The platform answers some accepted work with 202, and a fixture guard
    that only reads 200/201 reddens on success."""
    return 200 <= int(rc) < 300


def ids(kind: str) -> str:
    return ",".join(f"'{x}'" for x in MADE[kind]) or "'(none)'"


def check(name: str, passed: bool, detail: str = ""):
    checks.append((name, bool(passed), detail))
    print(f"  {'OK  ' if passed else 'FAIL'} {name}" + ("" if passed else f" — {detail}"))


TAG_LINE = re.compile(r"(INSERT \d+ \d+|UPDATE \d+|DELETE \d+|SELECT \d+|SET|BEGIN|COMMIT|ROLLBACK|"
                      r"RESET|GRANT|REVOKE|SAVEPOINT \S+|RELEASE \S+)")


def psql_argv(statement: str, guc: str | None = None) -> list[str]:
    argv = ["docker", "compose", "exec", "-T"]
    if guc:
        argv += ["-e", f"PGOPTIONS={guc}"]
    argv += ["postgres", "psql", "-U", "music_admin", "-d", "music", "-tAc", " ".join(statement.split())]
    return argv


def sql(statement: str, guc: str | None = None) -> str:
    out = subprocess.run(psql_argv(statement, guc), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()[:300]}")
    lines = [x.strip() for x in out.stdout.strip().splitlines() if x.strip()]
    if not lines:
        return ""
    if TAG_LINE.fullmatch(lines[0]):
        raise SystemExit(f"psql returned the command tag {lines[0]!r} instead of a value: "
                         f"{statement[:120]}")
    return lines[0]


def run(statement: str, guc: str | None = None) -> None:
    """A write whose returned row we do not need -- there a command tag is the expected answer."""
    out = subprocess.run(psql_argv(statement, guc), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql write failed: {out.stderr.strip()[:300]}")


def sql_err(statement: str, guc: str | None = None) -> str:
    """The database's own first refusal line -- the words, not a status code."""
    out = subprocess.run(psql_argv(statement, guc), capture_output=True, text=True)
    if out.returncode == 0:
        return ""
    lines = [x.strip() for x in out.stderr.splitlines() if x.strip()]
    # psql ends with "CONTEXT: PL/pgSQL function ... at RAISE", which names the function but not the
    # refusal. The words the platform wrote are on the ERROR line, and that is what a check must match.
    for line in lines:
        if line.startswith("ERROR:"):
            return line
    return lines[-1] if lines else ""


def login(cred):
    r = httpx.post(f"{BASE}/auth/login", json={"email": cred[0], "password": cred[1]}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"login failed for {cred[0]}: {r.status_code} {r.text[:160]}")
    data = r.json()
    return data["access_token"], data["workspaces"][0]["id"]


def call(method, path, token, ws, **kwargs):
    """(status, body) and never asserts -- the caller turns it into a check."""
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-Id": ws,
               "Content-Type": "application/json", **kwargs.pop("headers", {})}
    r = httpx.request(method, BASE + path, headers=headers, timeout=90, **kwargs)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.content


CAPS_OK = {
    "stream": {"status": "allowed", "reason": "Hold drill synthetic contract"},
    "download": {"status": "allowed", "reason": "Hold drill synthetic contract"},
    "share": {"status": "allowed", "reason": "Hold drill synthetic contract"},
    "commercial_use": {"status": "allowed", "reason": "Hold drill synthetic contract"},
    "license": {"status": "allowed", "reason": "Hold drill synthetic contract"},
    "sublicense": {"status": "blocked", "reason": "Not included"},
    "distribute": {"status": "allowed", "reason": "Hold drill synthetic contract"},
    "mint": {"status": "blocked", "reason": "Not included"},
    "content_id": {"status": "blocked", "reason": "Not included"},
}


def review(tok, ws, snap, legal_hold, note, evidence_ids):
    """The only real producer of the manifest flag. Each call issues a NEW manifest version.

    The door refuses to grant commercial capabilities without evidence behind them
    (`commercial capabilities require selected provider contract or license evidence`), so the
    evidence id is carried on every review, held or not -- a held review blocks the capabilities
    itself, and the unheld ones have to show their source.
    """
    caps = {k: dict(v) for k, v in CAPS_OK.items()}
    rc, body = call("POST", f"/assets/{snap}/rights-review", tok, ws,
                    json={"status": "verified", "capabilities": caps, "legal_hold": legal_hold,
                          "review_note": note, "evidence_ids": evidence_ids})
    if not ok2(rc):
        raise SystemExit(f"fixture: rights-review refused {rc} {str(body)[:200]}")
    keep("manifests", body["id"])
    return body


def make_marketable_asset(tok, ws, label):
    """project -> quote -> job -> master -> evidence -> clean reviewed manifest, all by the real doors."""
    rc, project = call("POST", "/projects", tok, ws,
                       json={"title": f"Hold drill {label} {STAMP}"})
    rc, quote = call("POST", f"/projects/{project['id']}/quotes", tok, ws,
                     json={"candidate_count": 1, "scenario": "success"})
    if not ok2(rc):
        raise SystemExit(f"fixture: quote refused {rc} {str(quote)[:240]}")
    rc, job = call("POST", "/jobs", tok, ws, headers={"Idempotency-Key": f"hold-{uuid.uuid4().hex}"},
                   json={"quote_id": quote["id"], "quote_hash": quote["quote_hash"],
                         "user_confirmation": True})
    if not ok2(rc) or "id" not in job:
        raise SystemExit(f"fixture: job refused {rc} {str(job)[:240]}")
    deadline, done = time.time() + 120, None
    while time.time() < deadline:
        rc, done = call("GET", f"/jobs/{job['id']}", tok, ws)
        if done.get("job", {}).get("status") in {"completed", "partial", "failed", "dead_letter"}:
            break
        time.sleep(1)
    ready = [c for c in (done or {}).get("candidates", []) if c["status"] == "ready"]
    if not ready:
        raise SystemExit(f"fixture: no ready candidate for {label} "
                         f"(job {(done or {}).get('job', {}).get('status')!r})")
    rc, asset = call("POST", f"/projects/{project['id']}/master", tok, ws,
                     json={"candidate_id": ready[0]["id"], "expected_spec_revision": 1,
                           "confirmation": True})
    if not ok2(rc) or "asset_snapshot_id" not in asset:
        raise SystemExit(f"fixture: master refused {rc} {str(asset)[:240]}")
    snap = asset["asset_snapshot_id"]
    rc, evidence = call("POST", f"/assets/{snap}/rights-evidence", tok, ws, json={
        "evidence_type": "provider_contract", "project_id": project["id"],
        "evidence": {"contract_id": f"hold-drill-{STAMP}-{label}", "scope": "test_only",
                     "provider": "synthetic_licensed"}})
    man = review(tok, ws, snap, False, f"Hold drill {label} {STAMP} review", [evidence["id"]])
    keep("projects", project["id"]); keep("snapshots", snap); keep("evidence", evidence["id"])
    for cand in ready:
        keep("candidates", cand["id"])
    for m in sql("SELECT coalesce(string_agg(media_asset_id::text,','),'') FROM audio_candidates"
                 f" WHERE id = '{ready[0]['id']}' AND media_asset_id IS NOT NULL").split(","):
        if m:
            keep("media", m)
    return {"project": project["id"], "snapshot": snap, "evidence": evidence["id"],
            "manifest": man["id"], "version": man["version"]}


def offer(tok, ws, snap, title, exclusive=False):
    """Creates an offer and registers it, so teardown can reach it by id."""
    rc, body = call("POST", "/offers", tok, ws,
                json={"asset_snapshot_id": snap, "title": title, "price_amount": 4900,
                      "currency": "USD", "territory": "worldwide", "duration_days": 365,
                       "exclusive": exclusive, "status": "active"})
    if ok2(rc):
        keep("offers", body.get("id"))
    return rc, body


def listed(offer_id) -> bool:
    return sql(f"SELECT count(*) FROM marketplace_offers_public WHERE id='{offer_id}'") == "1"


def reserve(offer_id, buyer_ws) -> str:
    """The refusal arms call the SECURITY DEFINER function directly: the hold predicate is reached
    before any row is inserted, so no order id has to exist for them."""
    return sql_err(f"SELECT reserve_marketplace_offer('{offer_id}','{uuid.uuid4()}','{uuid.uuid4()}')",
                   guc=f"-c app.workspace_id={buyer_ws}")


def case(tok, ws, snap, phase):
    rc, body = call("POST", "/moderation/cases", tok, ws, json={
        "subject_type": "asset", "subject_id": snap, "case_type": "copyright", "severity": "high",
        "evidence": {"drill": STAMP, "phase": phase}, "legal_hold": True})
    if not ok2(rc):
        raise SystemExit(f"fixture: case refused {rc} {str(body)[:200]}")
    return keep("cases", body["id"])


def release_case(tok, ws, case_id):
    return call("PUT", f"/moderation/cases/{case_id}", tok, ws,
                json={"status": "dismissed", "restrictions": {}, "legal_hold": False})


def main():
    print(f"legal hold drill {STAMP} against {BASE}")
    stok, sws = login(SELLER)
    btok, bws = login(BUYER)

    # Precondition, stated: three generations have to be payable, and an earlier run on the same
    # demo workspace can spend this away. `ensure_credits` buys the packs through the platform's own
    # `/orders/credits` door -- the same route provider_regression.py uses, on purpose.
    price = e2e_client.unit_price(BASE, stok, sws)
    funded = e2e_client.ensure_credits(BASE, stok, sws, 4 * price)
    check(f"the seller can pay for the three generations this drill needs ({price}/job)",
          funded >= 4 * price, f"available {funded} against {4 * price}")

    # ============================== 1. the case carrier, on both polarities
    a = make_marketable_asset(stok, sws, "case")
    rc, o1 = offer(stok, sws, a["snapshot"], f"Hold drill case offer {STAMP}")
    check("a reviewed, unheld asset can be listed (the arm that must NOT fire)", ok2(rc),
          f"{rc} {str(o1)[:200]}")
    check("and it appears in the public discovery view before anything is held", listed(o1["id"]),
          "expected one row in marketplace_offers_public")

    c1 = case(stok, sws, a["snapshot"], "phase1")
    check("the hold is on the stored row, not only in the response",
          sql(f"SELECT legal_hold FROM moderation_cases WHERE id='{c1}'") == "t",
          "moderation_cases.legal_hold did not read back true")
    check("a held case removes the offer from the public view", not listed(o1["id"]),
          "the row is still listed")
    msg = reserve(o1["id"], bws)
    check("the reservation function refuses with the case clause's own words",
          "asset is restricted" in msg, msg[:200])
    rc, body = call("PUT", f"/moderation/cases/{c1}", stok, sws,
                    json={"status": "open", "restrictions": {}, "legal_hold": False})
    check("clearing the flag alone does not restore the listing -- the predicate is flag OR status",
          not listed(o1["id"]), "an open case with legal_hold off still showed its offer")
    release_case(stok, sws, c1)
    check("dismissed and unheld, the offer is visible again (must NOT fire)", listed(o1["id"]),
          "still hidden after dismissal")
    rc, order0 = call("POST", f"/marketplace/offers/{o1['id']}/purchase", btok, bws,
                      json={"licensee_name": "Hold drill buyer one"})
    if ok2(rc):
        keep("orders", order0.get("id"))
    check("and the same purchase route now gets past the hold predicate", ok2(rc),
          f"{rc} {str(order0)[:200]}")

    # ============================== 2. the manifest carrier: reachable? measured, and it is not
    b = make_marketable_asset(stok, sws, "manifest")
    held = review(stok, sws, b["snapshot"], True, f"Hold drill manifest held {STAMP}", [b["evidence"]])
    check("the review door writes legal_hold into a NEW manifest version",
          sql(f"SELECT manifest->>'legal_hold' FROM rights_manifests WHERE id='{held['id']}'") == "true",
          "flag absent from the stored jsonb")
    cap = sql(f"SELECT manifest->'capabilities'->'commercial_use'->>'status' FROM rights_manifests"
              f" WHERE id='{held['id']}'")
    check("the same door blocks commercial_use in the same statement, so the flag never travels alone",
          cap == "blocked", f"commercial_use reads {cap!r}")
    rc, body = offer(stok, sws, b["snapshot"], f"Hold drill held offer {STAMP}")
    check("an offer cannot be created against a held manifest, and the door answers 409 with the "
          "capability clause's words",
          rc == 409 and "does not permit commercial licensing" in str(body), f"{rc} {str(body)[:200]}")
    both = json.loads(sql(f"SELECT jsonb_strip_nulls(jsonb_build_object("
                          f"'hold', manifest->>'legal_hold', 'cu', manifest->'capabilities'"
                          f"->'commercial_use'->>'status')) FROM rights_manifests WHERE id='{held['id']}'"))
    check("because the review door cannot leave a capability allowed while it sets the flag -- "
          "so assert_offer_rights's own legal_hold disjunct has no producer either (commerce.py:48)",
          both.get("hold") == "true" and both.get("cu") == "blocked", str(both))
    rc, restricted = call("POST", f"/assets/{b['snapshot']}/rights-review", stok, sws, json={
        "status": "restricted", "capabilities": {k: dict(v) for k, v in CAPS_OK.items()},
        "legal_hold": False, "review_note": f"Hold drill restricted {STAMP}",
        "evidence_ids": [b["evidence"]]})
    rc2, note_body = offer(stok, sws, b["snapshot"], f"Hold drill restricted offer {STAMP}")
    note_msg = str(note_body)
    check("commerce.py:48 is not dead code -- with capabilities left allowed and the status restricted, "
          "that line is the one that answers",
          ok2(rc) and "asset is restricted or under legal hold" in note_msg, note_msg[:200])
    review(stok, sws, b["snapshot"], False, f"Hold drill manifest clean {STAMP}", [b["evidence"]])
    rc, o2 = offer(stok, sws, b["snapshot"], f"Hold drill clean offer {STAMP}")
    check("unheld again, the same door lists it (must NOT fire)", ok2(rc), f"{rc} {str(o2)[:200]}")

    held2 = review(stok, sws, b["snapshot"], True, f"Hold drill manifest held again {STAMP}", [b["evidence"]])
    msg = reserve(o2["id"], bws)
    check("a hold arriving AFTER the offer is answered as staleness, not as the hold clause -- ordering",
          "offer rights manifest is stale" in msg and "not licensable" not in msg, msg[:200])
    err = sql_err(f"UPDATE rights_manifests SET manifest=jsonb_set(manifest,'{{legal_hold}}','true')"
                  f" WHERE id='{o2['rights_manifest_id']}'")
    check("the pinned manifest cannot be rewritten in place to reach that clause (trigger refuses UPDATE)",
          "rights_manifests is immutable" in err, err[:200])
    pinned = sql("SELECT count(*) FROM asset_offers o JOIN rights_manifests r ON r.id=o.rights_manifest_id"
                 " WHERE (r.manifest->>'legal_hold')::boolean")
    check("so no offer in the database is pinned to a held manifest: the flag clause has no producer",
          pinned == "0", f"{pinned} offers pinned to held manifests")
    check("...which makes 002's manifest-hold line defence in depth rather than a live path (recorded)",
          pinned == "0" and "immutable" in err and "stale" in msg, "the three arms above must agree")

    # ============================== 3. the paid path, and the doors a hold does NOT close
    c = make_marketable_asset(stok, sws, "paid")
    rc, o3 = offer(stok, sws, c["snapshot"], f"Hold drill paid offer {STAMP}")
    rc, order = call("POST", f"/marketplace/offers/{o3['id']}/purchase", btok, bws,
                     json={"licensee_name": "Hold drill buyer"})
    if ok2(rc):
        keep("orders", order.get("id"))
    rc, paid = call("POST", f"/orders/{order['id']}/pay", btok, bws,
                    headers={"Idempotency-Key": f"hold-pay-{uuid.uuid4().hex}"},
                    json={"scenario": "success"})
    status, deadline = None, time.time() + 45
    while time.time() < deadline:
        rc, orders = call("GET", "/orders", btok, bws)
        status = next((x["status"] for x in orders.get("items", []) if x["id"] == order["id"]), None)
        if status in {"fulfilled", "failed", "refunded"}:
            break
        time.sleep(1)
    check("the order fulfils, so there is a real licence and delivery to test against",
          status == "fulfilled", f"order status {status!r}")
    rc, lics = call("GET", "/licenses", btok, bws)
    lic = next((x for x in lics.get("items", []) if x["order_id"] == order["id"]), {})
    rc, dels = call("GET", "/deliveries", btok, bws)
    dlv = next((x for x in dels.get("items", []) if x["order_id"] == order["id"]), {})
    check("an active licence exists before the hold lands (the premise for the next four checks)",
          lic.get("status") == "active" and bool(dlv.get("id")), f"licence {str(lic)[:120]}")
    splits_key = f"subject_type='license' AND subject_id='{lic.get('id')}'"
    splits_before = sql(f"SELECT count(*) FROM revenue_splits WHERE {splits_key}")

    c2 = case(stok, sws, c["snapshot"], "post-sale")
    rc, exp = call("GET", f"/deliveries/{dlv['id']}/export", btok, bws)
    check("DOWNLOAD/EXPORT IS NOT BLOCKED: the delivered package still comes out after the hold",
          rc == 200 and isinstance(exp, bytes) and exp[:2] == b"PK", f"{rc} {str(exp)[:120]}")
    rc, lics2 = call("GET", "/licenses", btok, bws)
    lic2 = next((x for x in lics2.get("items", []) if x["order_id"] == order["id"]), {})
    check("AN ISSUED LICENCE IS NOT REVOKED by a later hold", lic2.get("status") == "active",
          str(lic2)[:160])
    rc, seller = call("GET", f"/assets/{c['snapshot']}/export", stok, sws)
    check("THE SELLER'S OWN EXPORT IS NOT BLOCKED either (rights.py reads capability status only)",
          rc == 200, f"{rc} {str(seller)[:120]}")
    splits_after = sql(f"SELECT count(*) FROM revenue_splits WHERE {splits_key}")
    check("and the hold does not reach the money: the settlement rows are the same count, and not zero",
          splits_after == splits_before and int(splits_before or 0) > 0,
          f"splits {splits_before} before, {splits_after} after")

    # ============================== 4. brand award: the case clause, both ways
    rc, brief = call("POST", "/brand-briefs", btok, bws, json={
        "title": f"Hold drill brief {STAMP}", "description": "Award gate for the hold drill.",
        "budget_amount": 3000, "currency": "USD", "requirements": {"usage": "hold drill"},
        "status": "open"})
    if ok2(rc):
        keep("briefs", brief.get("id"))
    rc, sub = call("POST", f"/brand-briefs/{brief['id']}/submissions", stok, sws,
                   json={"asset_snapshot_id": c["snapshot"], "notes": f"hold drill {STAMP}"})
    if ok2(rc):
        keep("submissions", sub.get("id"))
    check("a submission is accepted against a held asset -- the refusal belongs to the award, not here",
          ok2(rc), f"{rc} {str(sub)[:200]}")
    err = sql_err(f"SELECT count(*) FROM prepare_brand_award('{sub['id']}')",
                  guc=f"-c app.workspace_id={bws}")
    check("the award refuses the held asset with the case wording",
          "submission asset is restricted" in err, err[:200])
    release_case(stok, sws, c2)
    rows = sql(f"SELECT count(*) FROM prepare_brand_award('{sub['id']}')",
               guc=f"-c app.workspace_id={bws}")
    check("released, the very same call returns the award row (must NOT fire)", rows == "1",
          f"prepared rows: {rows!r}")

    # ============================== 5. the denominator, read from the catalog rather than from files
    readers = sorted(x for x in sql(
        "SELECT string_agg(name,',') FROM ("
        " SELECT proname AS name FROM pg_proc WHERE prosrc LIKE '%legal_hold%'"
        " UNION SELECT c.relname FROM pg_class c WHERE c.relkind='v'"
        "   AND pg_get_viewdef(c.oid,true) LIKE '%legal_hold%') x").split(",") if x)
    check("the catalog's whole list of readers is exactly the three market objects",
          readers == ["marketplace_offers_public", "prepare_brand_award", "reserve_marketplace_offer"],
          f"catalog says {readers}")
    check("the live award body is 009's qualified rewrite, which a file-only reader would miss",
          sql("SELECT prosrc LIKE '%rights_manifests.asset_snapshot_id=sub.asset_snapshot_id%'"
              " FROM pg_proc WHERE proname='prepare_brand_award'") == "t",
          "009's qualified predicate is not in the live body")
    check("no erasure path consults the flag",
          sql("SELECT count(*) FROM pg_proc WHERE prosrc LIKE '%legal_hold%'"
              " AND proname NOT IN ('reserve_marketplace_offer','prepare_brand_award')") == "0",
          "a function outside the two market doors now reads the flag")

    # ============================== 6. the append-only half: what protects, and what does not
    err = sql_err(f"DELETE FROM rights_evidence WHERE id='{c['evidence']}'")
    check("rights evidence refuses DELETE even to a superuser role (the guard that works)",
          "rights_evidence is immutable" in err, err[:200])
    lic_id = sql(f"SELECT id FROM licenses WHERE order_id='{order['id']}' LIMIT 1")
    err = sql_err(f"DELETE FROM licenses WHERE id='{lic_id}'")
    check("an issued licence refuses DELETE as well", "licenses is immutable" in err, err[:200])

    # The 002 pair refused DELETE only, so the *content* of an evidence row and the *terms* of a licence
    # stayed writable -- that is why docs/RIGHTS_POLICY.md's "保留所有证据" sentence was only partly true.
    # Migration 022 froze exactly the columns the policy names as immutable and left the two shapes the
    # product itself writes. Both directions are measured here, because a freeze that also refuses the
    # product's own write is a broken feature, not a guard -- and that is what the first attempt at 022
    # did: a NULL trigger argument (plpgsql's TG_ARGV is 0-based) made every UPDATE raise before the
    # frozen-column message could even be reached.
    err = sql_err(f"UPDATE rights_evidence SET evidence='{{\"drill\":1}}'::jsonb WHERE id='{c['evidence']}'")
    check("evidence content refuses UPDATE and names the column that moved",
          "frozen" in err and "attempted change: evidence" in err, err[:200])
    err = sql_err(f"UPDATE rights_evidence SET status='verified',reviewed_at=now() WHERE id='{c['evidence']}'")
    check("the reviewer's own write still lands (the shape services/api/app/routers/assets.py:217 sends)",
          err == "", err[:200])
    err = sql_err(f"UPDATE licenses SET territory='Nowhere' WHERE id='{lic_id}'")
    check("a licence's territory refuses UPDATE", "attempted change: territory" in err, err[:200])
    err = sql_err(f"UPDATE licenses SET license_hash=repeat('0',64) WHERE id='{lic_id}'")
    check("the hash that identifies the grant refuses UPDATE too", "attempted change: license_hash" in err,
          err[:200])
    err = sql_err(f"UPDATE licenses SET status='suspended' WHERE id='{lic_id}'")
    check("the refund/suspension write the platform performs still lands (market.py:372's shape)",
          err == "", err[:200])
    wired = sql("SELECT count(*) FROM (VALUES"
                " ('immutable_rights_evidence_content','{status,reviewed_by,reviewed_at}'),"
                " ('immutable_license_terms','{status,activated_at}')) v(name,args)"
                " JOIN pg_trigger t ON t.tgname=v.name"
                " WHERE t.tgargs = convert_to(v.args,'UTF8') || '\\x00'::bytea")
    check("both freeze triggers carry the column list this file reads", wired == "2",
          f"{wired} of 2 carry their expected list; pg_trigger stores the argument as one NUL-terminated "
          f"bytea, and an absent list makes the guard refuse every UPDATE -- a broken feature, not a guard")
    guardless = sql("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid"
                    " WHERE c.relname='media_assets' AND t.tgtype & 8 = 8 AND NOT tgisinternal")
    check("MEDIA ASSETS HAS NO DELETE TRIGGER AT ALL, so a held asset's bytes are deletable",
          guardless == "0", f"{guardless} delete triggers on media_assets")
    probe = str(uuid.uuid4())
    run("INSERT INTO media_assets(id,workspace_id,kind,bucket,object_key,sha256,mime_type,bytes,"
        "scan_status) VALUES('" + probe + "','" + sws + "','master','hold-drill','" + probe + "',"
        "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef','audio/wav',1,'unscanned')")
    check("the probe row is in media_assets before anything is attempted",
          sql(f"SELECT count(*) FROM media_assets WHERE id='{probe}'") == "1", "insert did not land")
    armed = sql_err(f"UPDATE media_assets SET scan_status='clean' WHERE id='{probe}'")
    check("this session's triggers ARE armed -- the same table refuses an unsupported scan_status "
          "write, so a delete that lands below is a missing guard rather than a disabled one",
          "must name the engine" in armed, armed[:200])
    err = sql_err(f"DELETE FROM media_assets WHERE id='{probe}'")
    check("...measured, not inferred: a DELETE of a media asset lands with those triggers armed",
          err == "" and sql(f"SELECT count(*) FROM media_assets WHERE id='{probe}'") == "0",
          f"delete said: {err[:160]}")

    # ============================== 7. teardown, then prove it
    teardown()
    for kind, table in (("cases", "moderation_cases"), ("offers", "asset_offers"),
                        ("briefs", "brand_briefs"), ("submissions", "brand_submissions")):
        left = sql(f"SELECT count(*) FROM {table} WHERE id IN ({ids(kind)})")
        check(f"teardown removed every {kind} row this run made", left == "0",
              f"{left} of {len(MADE[kind])} remain: {MADE[kind]}")
    trail = sql(f"SELECT count(*) FROM orders WHERE id IN ({ids('orders')})")
    check("the financial trail is left in place on purpose and is still whole (see teardown docstring)",
          trail == str(len(MADE["orders"])), f"{trail} of {len(MADE['orders'])} orders remain")

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\nlegal hold drill: {passed}/{len(checks)} checks passed")
    metrics.emit(checks_passed=passed, checks_total=len(checks))
    for name, ok, detail in checks:
        if not ok:
            print(f"  FAILED: {name} — {detail}")
    return 0 if passed == len(checks) else 1


DISARM = "BEGIN; SET LOCAL session_replication_role = replica; "


def teardown():
    """Remove this run's market and moderation rows, by the ids it registered.

    Deliberately NOT removed: the financial trail (order, licence, delivery, split, ledger entries).
    Deleting rows behind a settled ledger would move the tenant's absolute balances that
    `scripts/restore_fidelity.py` fingerprints and `scripts/reconcile_market.py` conserves, so the
    residue is asserted to be consistent instead of erased -- which is also what every other
    commercial script on this chain already does.
    """
    statements = [
        f"DELETE FROM offer_reservations WHERE offer_id IN ({ids('offers')})",
        f"DELETE FROM asset_offers WHERE id IN ({ids('offers')})",
        f"DELETE FROM brand_submissions WHERE id IN ({ids('submissions')})",
        f"DELETE FROM brand_briefs WHERE id IN ({ids('briefs')})",
        f"DELETE FROM moderation_cases WHERE id IN ({ids('cases')})",
    ]
    for stmt in statements:
        out = subprocess.run(psql_argv(DISARM + stmt + "; COMMIT;"), capture_output=True, text=True)
        if out.returncode != 0:
            first = (out.stderr.splitlines() or [""])[-1]
            print(f"  teardown note: {stmt[:64]} -> {first[:160]}")


if __name__ == "__main__":
    sys.exit(main())
