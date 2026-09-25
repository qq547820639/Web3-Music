#!/usr/bin/env python3
"""License / delivery / refund / payout reconciliation (release gate G12-b).

Nothing compared these four tables today. test_commercial.py:54 walks one happy path and
asserts each row's *status* in isolation, so a licence issued with no payout, a payout whose
amount disagrees with the 85/15 split, or a refund that revoked the delivery but left the
payout pending would all pass.

Two readers are used on purpose: Postgres holds the authority and the HTTP API holds what
the product shows, so a check can fail on drift in either direction (a copy-vs-copy check
would only prove the two copies agree).
"""
from __future__ import annotations

import re
import subprocess
import time
import uuid
from decimal import Decimal

import e2e_client
import httpx

BASE = "http://localhost:8000/api"
SELLER = ("owner@example.local", "demo-owner")
BUYER = ("viewer@other.local", "demo-viewer")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
POLICY_SELLER_BP = 8500


def psql_rows(sql: str):
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "music_admin",
                          "-d", "music", "-tA", "-F", "\x1f", "-c", sql],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()[:300]}")
    rows = []
    for line in out.stdout.splitlines():
        if line.strip():
            rows.append(line.split("\x1f"))
    return rows


def login(creds):
    return e2e_client.login(BASE, creds[0], creds[1])


def get(path, sess):
    token, ws = sess
    r = httpx.get(BASE + path, headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws}, timeout=40)
    r.raise_for_status()
    return r.json()


def make_sale(seller, buyer, exclusive=False):
    """Drive one complete exclusive-free licence sale so the invariants have a subject."""
    project = httpx.post(BASE + "/projects", json={"title": "Reconcile " + uuid.uuid4().hex[:6]},
                         headers=_h(seller), timeout=40).json()
    quote = httpx.post(BASE + f"/projects/{project['id']}/quotes",
                       json={"candidate_count": 1, "scenario": "success"}, headers=_h(seller), timeout=40).json()
    job = httpx.post(BASE + "/jobs", headers={**_h(seller), "Idempotency-Key": "rec-" + uuid.uuid4().hex},
                     json={"quote_id": quote["id"], "quote_hash": quote["quote_hash"], "user_confirmation": True},
                     timeout=40).json()
    end = time.time() + 90
    detail = {}
    while time.time() < end:
        detail = httpx.get(BASE + f"/jobs/{job['id']}", headers=_h(seller), timeout=40).json()
        if detail["job"]["status"] in {"completed", "partial", "failed", "dead_letter"}:
            break
        time.sleep(1)
    ready = [c for c in detail.get("candidates", []) if c["status"] == "ready"]
    if not ready:
        raise SystemExit("reconciliation needs a ready candidate")
    asset = httpx.post(BASE + f"/projects/{project['id']}/master",
                       json={"candidate_id": ready[0]["id"], "expected_spec_revision": 1, "confirmation": True},
                       headers=_h(seller), timeout=40).json()
    asset_id = asset["asset_snapshot_id"]
    evidence = httpx.post(BASE + f"/assets/{asset_id}/rights-evidence",
                          json={"evidence_type": "provider_contract", "project_id": project["id"],
                                "evidence": {"contract_id": "synthetic-reconcile", "scope": "test_only"}},
                          headers=_h(seller), timeout=40).json()
    caps = {n: {"status": "allowed" if n in {"stream", "download", "share", "commercial_use", "license", "distribute"} else "blocked",
                "reason": "reconcile"}
            for n in ("stream", "download", "share", "commercial_use", "license", "sublicense", "distribute", "mint", "content_id")}
    httpx.post(BASE + f"/assets/{asset_id}/rights-review",
               json={"status": "verified", "capabilities": caps, "legal_hold": False,
                     "review_note": "reconcile", "evidence_ids": [evidence["id"]]},
               headers=_h(seller), timeout=40).raise_for_status()
    price = 3777  # deliberately not divisible by 20 so floor() rounding is exercised
    offer = httpx.post(BASE + "/offers",
                       json={"asset_snapshot_id": asset_id, "title": "Reconcile offer", "price_amount": price,
                             "currency": "USD", "territory": "worldwide", "duration_days": 30,
                             "exclusive": exclusive, "status": "active"},
                       headers=_h(seller), timeout=40).json()
    order = httpx.post(BASE + f"/marketplace/offers/{offer['id']}/purchase",
                       json={"licensee_name": "Reconcile buyer"}, headers=_h(buyer), timeout=40).json()
    httpx.post(BASE + f"/orders/{order['id']}/pay", headers={**_h(buyer), "Idempotency-Key": "rec-pay-" + uuid.uuid4().hex},
               json={"scenario": "success"}, timeout=40).raise_for_status()
    end = time.time() + 40
    while time.time() < end:
        row = next((x for x in get("/orders", buyer).get("items", []) if x["id"] == order["id"]), None)
        if row and row["status"] == "fulfilled":
            return order["id"], price, offer["id"]
        time.sleep(.5)
    raise SystemExit("reconciliation order never fulfilled")


def _h(sess, extra=None):
    token, ws = sess
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": ws, "Content-Type": "application/json",
            **(extra or {})}


def policy_failures(order_total: int, payout_amount: int, splits: dict[str, int]) -> list[str]:
    """The 85/15 money invariants, as a pure function so they can be tested both ways.

    Returns one string per violated invariant, and [] only for a compliant settlement.
    """
    out = []
    expected_seller = int(Decimal(order_total) * POLICY_SELLER_BP / 10000)
    if payout_amount != expected_seller:
        out.append(f"payout {payout_amount} != 85% share {expected_seller} of {order_total}")
    if set(splits) != {"platform", "workspace"}:
        out.append(f"expected a workspace and a platform split, found {sorted(splits)}")
    if sum(splits.values()) != 10000:
        out.append(f"basis points sum to {sum(splits.values())}, not 10000: {splits}")
    if splits.get("workspace") != POLICY_SELLER_BP:
        out.append(f"workspace split is {splits.get('workspace')}bp, policy is {POLICY_SELLER_BP}bp")
    platform_share = order_total - payout_amount
    if platform_share < 0:
        out.append(f"payout {payout_amount} exceeds order total {order_total}")
    if platform_share * 10000 < order_total * 1500:
        out.append(f"platform share {platform_share} under-receives on an order of {order_total}")
    return out


CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        print(f"RECONCILE FAIL [{name}]: {detail}", flush=True)


def offer_status(offer_id):
    if not UUID_RE.match(str(offer_id)):
        raise SystemExit(f"unexpected offer id {offer_id!r}")
    return psql_rows(f"SELECT status FROM asset_offers WHERE id='{offer_id}'")[0][0]


def main():
    seller, buyer = login(SELLER), login(BUYER)
    credit_price = e2e_client.unit_price(BASE, *seller)
    e2e_client.ensure_credits(BASE, *seller, 6 * credit_price)
    order_id, price, offer_id = make_sale(seller, buyer)
    for value in (order_id,):
        if not UUID_RE.match(str(value)):
            raise SystemExit(f"unexpected identifier {value!r}")
    time.sleep(2)  # the fulfilment webhook is asynchronous

    licence = psql_rows("SELECT l.id, l.order_id, l.seller_workspace_id, l.buyer_workspace_id, l.status,"
                        " o.total, o.status FROM licenses l JOIN orders o ON o.id=l.order_id "
                        f"WHERE l.order_id='{order_id}'")
    if len(licence) != 1:
        raise SystemExit(f"expected exactly one licence for order {order_id}, found {len(licence)}")
    license_id, _lic_order, _seller_ws, _buyer_ws, license_status, order_total, order_status = licence[0]
    order_total = int(order_total)

    check("order fulfilled and licence active",
          order_status == "fulfilled" and license_status == "active",
          f"order={order_status} licence={license_status}")

    deliveries = psql_rows(f"SELECT count(*) FROM deliveries WHERE license_id='{license_id}'")[0][0]
    check("exactly one delivery per licence", deliveries == "1", f"deliveries={deliveries}")

    payouts = psql_rows("SELECT id, amount, status, currency FROM payouts "
                        f"WHERE reference_type='license' AND reference_id='{license_id}'")
    check("exactly one seller payout per licence", len(payouts) == 1, f"payout rows={len(payouts)}")
    if len(payouts) != 1:
        return _report()
    payout_id, payout_amount, payout_status, payout_currency = payouts[0]
    check("payout currency follows the order", payout_currency == "USD", f"currency={payout_currency}")
    check("payout pending before settlement", payout_status == "pending", f"status={payout_status}")

    splits = psql_rows("SELECT beneficiary_type, basis_points FROM revenue_splits "
                       "WHERE subject_type='license' AND subject_id='" + license_id + "' ORDER BY beneficiary_type")
    by_beneficiary = {row[0]: int(row[1]) for row in splits}
    check("licence has both seller and platform splits",
          set(by_beneficiary) == {"platform", "workspace"}, f"splits={by_beneficiary}")
    arithmetic = policy_failures(order_total, int(payout_amount), by_beneficiary)
    check("revenue splits and payout satisfy the 85/15 policy", not arithmetic, "; ".join(arithmetic))

    # API side: the product must show the same numbers the database holds.
    api_payouts = get("/payouts", seller).get("items", [])
    api_payout = next((p for p in api_payouts if p["id"] == payout_id), None)
    check("seller API lists the payout", api_payout is not None, f"api payout ids={[p.get('id') for p in api_payouts][:5]}")
    if api_payout:
        check("API payout amount matches the database",
              int(api_payout["amount"]) == int(payout_amount),
              f"api={api_payout['amount']} db={payout_amount}")
    api_licences = get("/licenses", seller).get("items", [])
    check("seller API lists the licence it sold",
          any(l["id"] == license_id for l in api_licences), f"api licences={[l['id'] for l in api_licences][:5]}")
    check("buyer cannot see the seller's payout",
          not any(p["id"] == payout_id for p in get("/payouts", buyer).get("items", [])),
          "buyer listed the seller payout")

    # Refund side.
    httpx.post(BASE + f"/orders/{order_id}/refunds", headers={**_h(buyer), "Idempotency-Key": "rec-refund-" + uuid.uuid4().hex},
               json={"reason": "reconciliation reversal"}, timeout=40).raise_for_status()
    end = time.time() + 40
    while time.time() < end:
        row = psql_rows(f"SELECT status FROM orders WHERE id='{order_id}'")[0][0]
        if row == "refunded":
            break
        time.sleep(.5)
    after = psql_rows("SELECT o.status, l.status, d.status, p.status FROM orders o "
                      "JOIN licenses l ON l.order_id=o.id JOIN deliveries d ON d.license_id=l.id "
                      "JOIN payouts p ON p.reference_type='license' AND p.reference_id=l.id::text "
                      f"WHERE o.id='{order_id}'")[0]
    check("refund reverses the whole chain together",
          after[0] == "refunded" and after[1] == "refunded" and after[2] == "revoked" and after[3] == "reversed",
          f"order={after[0]} licence={after[1]} delivery={after[2]} payout={after[3]}")

    # Second subject: an exclusive listing, whose own state machine only moves for
    # exclusive offers, so the non-exclusive sale above cannot detect drift in it.
    ex_order, _ex_price, ex_offer = make_sale(seller, buyer, exclusive=True)
    check("paid exclusive offer is marked sold", offer_status(ex_offer) == "sold",
          f"offer after fulfilment = {offer_status(ex_offer)}")
    httpx.post(BASE + f"/orders/{ex_order}/refunds",
               headers={**_h(buyer), "Idempotency-Key": "rec-ex-refund-" + uuid.uuid4().hex},
               json={"reason": "exclusive refund"}, timeout=40).raise_for_status()
    deadline = time.time() + 40
    while time.time() < deadline:
        if psql_rows(f"SELECT status FROM orders WHERE id='{ex_order}'")[0][0] == "refunded":
            break
        time.sleep(.5)
    # market.py:376 is the write that lifts a sold exclusive listing off the shelf again.
    after_refund = offer_status(ex_offer)
    check("refunded exclusive offer is lifted out of the sold state", after_refund == "paused",
          f"offer still '{after_refund}' after the licence was refunded; the buyer's transaction "
          f"cannot see the seller's asset_offers row under RLS, so that UPDATE matches zero rows")

    recon = get("/admin/v12/reconciliation", seller)
    check("admin reconciliation endpoint passes for the seller",
          recon["status"] == "passed", f"status={recon['status']} checks={recon['checks']}")
    return _report()


def _report():
    bad = [name for name, ok, _ in CHECKS if not ok]
    print(f"market reconciliation: {len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed", flush=True)
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
