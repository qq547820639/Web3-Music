#!/usr/bin/env python3
"""Exclusive-offer double-sell drill (release gate G12-a).

reserve_marketplace_offer (db/migrations/002) locks the offer row and refuses a second
reservation while one is pending, so the reservation step is already serialized. The
fulfilment step is not: market.py:175-176 promotes a reservation to 'confirmed' and the
offer to 'sold' with no source-state predicate, so a buyer who is still holding an order
created before its reservation expired can pay it afterwards and still be issued a licence.

Sequence driven here:
  1. seller lists an exclusive offer
  2. buyer one reserves it (order created, unpaid)
  3. buyer two is refused            <- the guard that exists
  4. buyer one's reservation ages out (DB write, standing in for 30 real minutes)
  5. buyer two reserves and pays     <- correctly gets the exclusive licence
  6. buyer one pays the stale order  <- must not produce a second licence

Run against the commercial-test overlay: an exclusive offer needs approved commercial rights,
which the default emulator snapshot deliberately withholds.
"""
from __future__ import annotations

import re
import subprocess
import time
import uuid

import e2e_client
import httpx

BASE = "http://localhost:8000/api"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

SELLER = ("owner@example.local", "demo-owner")
BUYER_ONE = ("viewer@other.local", "demo-viewer")
BUYER_TWO = ("third@example.local", "demo-viewer")
ALLOWED = {"stream", "download", "share", "commercial_use", "license", "distribute"}


def fail(msg: str):
    print(f"RESERVATION RACE FAIL: {msg}", flush=True)
    raise SystemExit(1)


def login(creds):
    return e2e_client.login(BASE, creds[0], creds[1])


def call(method, path, sess, expect=(200, 201, 202, 204), **kwargs):
    token, ws = sess
    r = httpx.request(method, BASE + path,
                      headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws,
                               "Content-Type": "application/json", **(kwargs.pop("headers", None) or {})},
                      timeout=40, **kwargs)
    if r.status_code not in expect:
        raise SystemExit(f"{method} {path} -> {r.status_code} {r.text[:300]}")
    return r


def psql(sql: str) -> str:
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "music_admin",
                          "-d", "music", "-tAc", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def checked(value, what: str) -> str:
    if not UUID_RE.match(str(value)):
        fail(f"{what} is not a uuid: {value!r}")
    return str(value)


def wait_order(order_id, sess, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        row = next((x for x in call("GET", "/orders", sess).json().get("items", []) if x["id"] == order_id), None)
        if row and row["status"] in {"fulfilled", "refunded", "failed", "cancelled"}:
            return row
        time.sleep(.5)
    fail(f"order {order_id} never reached a terminal status")


def make_exclusive_offer(seller):
    project = call("POST", "/projects", seller, json={"title": "Race " + uuid.uuid4().hex[:6]}).json()
    pid = checked(project["id"], "project id")
    quote = call("POST", f"/projects/{pid}/quotes", seller, json={"candidate_count": 1, "scenario": "success"}).json()
    job = call("POST", "/jobs", seller, headers={"Idempotency-Key": "race-job-" + uuid.uuid4().hex},
               json={"quote_id": quote["id"], "quote_hash": quote["quote_hash"], "user_confirmation": True}).json()
    detail, end = None, time.time() + 90
    while time.time() < end:
        detail = call("GET", f"/jobs/{job['id']}", seller).json()
        if detail["job"]["status"] in {"completed", "partial", "failed", "dead_letter"}:
            break
        time.sleep(1)
    ready = [c for c in (detail or {}).get("candidates", []) if c["status"] == "ready"]
    if not ready:
        fail("generation produced no ready candidate to sell")
    asset_id = checked(call("POST", f"/projects/{pid}/master", seller,
                            json={"candidate_id": ready[0]["id"], "expected_spec_revision": 1,
                                  "confirmation": True}).json()["asset_snapshot_id"], "asset id")
    evidence = call("POST", f"/assets/{asset_id}/rights-evidence", seller,
                    json={"evidence_type": "provider_contract", "project_id": pid,
                          "evidence": {"contract_id": "synthetic-race-test", "scope": "test_only"}}).json()
    caps = {name: {"status": "allowed" if name in ALLOWED else "blocked",
                   "reason": "race drill" if name in ALLOWED else "excluded"}
            for name in ("stream", "download", "share", "commercial_use", "license",
                         "sublicense", "distribute", "mint", "content_id")}
    call("POST", f"/assets/{asset_id}/rights-review", seller,
         json={"status": "verified", "capabilities": caps, "legal_hold": False,
               "review_note": "race drill", "evidence_ids": [checked(evidence["id"], "evidence id")]})
    offer = call("POST", "/offers", seller,
                 json={"asset_snapshot_id": asset_id, "title": "Exclusive race offer", "price_amount": 3000,
                       "currency": "USD", "territory": "worldwide", "duration_days": 90,
                       "exclusive": True, "status": "active"}).json()
    return asset_id, checked(offer["id"], "offer id")


def reserve(sess, offer_id, label):
    order = call("POST", f"/marketplace/offers/{offer_id}/purchase", sess,
                 json={"licensee_name": label}).json()
    return checked(order["id"], "order id")


def settle(order_id, sess):
    call("POST", f"/orders/{order_id}/pay", sess,
         headers={"Idempotency-Key": "race-pay-" + uuid.uuid4().hex}, json={"scenario": "success"})
    return wait_order(order_id, sess)


def active_licences(asset_id):
    return int(psql(f"SELECT count(*) FROM licenses WHERE asset_snapshot_id='{asset_id}'::uuid "
                    "AND status='active'"))


def main():
    seller, buyer_one, buyer_two = login(SELLER), login(BUYER_ONE), login(BUYER_TWO)
    price = e2e_client.unit_price(BASE, *seller)
    e2e_client.ensure_credits(BASE, *seller, 6 * price)
    asset_id, offer_id = make_exclusive_offer(seller)

    order_one = reserve(buyer_one, offer_id, "Buyer One")
    if active_licences(asset_id) != 0:
        fail("control broken: an unpaid reservation already produced a licence")

    # While an exclusive offer carries a live reservation the public catalog view hides it
    # (marketplace_offers_public excludes it), so buyer two is refused with 404; the seller's
    # own reserve_marketplace_offer raises 409 if the row is somehow still listed.
    blocked = call("POST", f"/marketplace/offers/{offer_id}/purchase", buyer_two,
                   json={"licensee_name": "Buyer Two"}, expect=(201, 404, 409))
    if blocked.status_code == 201:
        fail("buyer two reserved an offer already reserved by buyer one")

    psql(f"UPDATE offer_reservations SET expires_at = now() - interval '1 minute' "
         f"WHERE order_id = '{order_one}'::uuid")

    order_two = settle(reserve(buyer_two, offer_id, "Buyer Two"), buyer_two)
    if active_licences(asset_id) != 1:
        fail(f"expected exactly one licence after buyer two won, found {active_licences(asset_id)}")

    late = call("POST", f"/orders/{order_one}/pay", buyer_one,
                headers={"Idempotency-Key": "race-late-" + uuid.uuid4().hex},
                json={"scenario": "success"}, expect=(200, 202, 409))

    # The stale order is deliberately NOT waited on: the correct outcome is that it never
    # fulfils, so waiting for a terminal status would be asserting the wrong contract. Give
    # any in-flight webhook a settle window, then prove no second licence appeared.
    time.sleep(6)
    count = active_licences(asset_id)
    if count != 1:
        fail(f"exclusive asset {asset_id} carries {count} active licences; stale order {order_one} "
             f"was paid after its reservation expired and still issued a licence alongside {order_two}")
    status = psql(f"SELECT status FROM orders WHERE id='{order_one}'")
    print(f"reservation race passed: {asset_id} has exactly one active licence ({order_two}); "
          f"the stale payment {late.status_code} left order {order_one} at '{status}' with no licence",
          flush=True)


if __name__ == "__main__":
    main()
