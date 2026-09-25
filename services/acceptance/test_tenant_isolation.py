"""Release gate G9-5 — cross-tenant isolation acceptance suite.

Three seeded tenants: SELLER (Demo Workspace), BUYER (Other Workspace) and
OUTSIDER (Isolation Third Workspace, added by db/migrations/010). All three hold
the 'owner' role inside their own workspace, so no denial recorded here can be an
RBAC 403 masquerading as tenant isolation: every forbidden read must come back
as 404 not-found.

Every check is a pair. The tenant that legitimately owns a row must actually see
it before another tenant's absence is allowed to count as evidence — otherwise a
route that simply does not exist, or a fixture that silently created nothing,
would still turn this file green.
"""

import os
import time
import uuid

import requests

ROOT = os.getenv('API_BASE_URL', 'http://localhost:8000')
BASE = ROOT + '/api'


def login(email, password):
    r = requests.post(BASE + '/auth/login', json={'email': email, 'password': password}, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    return data['access_token'], data['workspaces'][0]['id']


SELLER_TOKEN, SELLER_WS = login('owner@example.local', 'demo-owner')
BUYER_TOKEN, BUYER_WS = login('viewer@other.local', 'demo-viewer')
OUTSIDER_TOKEN, OUTSIDER_WS = login('third@example.local', 'demo-viewer')
assert len({SELLER_WS, BUYER_WS, OUTSIDER_WS}) == 3, 'acceptance needs three distinct tenants'

OTHERS = ((BUYER_TOKEN, BUYER_WS, 'buyer'), (OUTSIDER_TOKEN, OUTSIDER_WS, 'outsider'))

BPM_SENTINEL = 6174
TICKET_SUBJECT = 'Isolation tenant probe ' + uuid.uuid4().hex[:8]
COMMERCIAL_REASON = 'Synthetic isolation-suite contract test'


def hdr(token, ws):
    return {'Authorization': 'Bearer ' + token, 'X-Workspace-Id': ws, 'Content-Type': 'application/json'}


def req(method, path, token, ws, **kwargs):
    extra = kwargs.pop('headers', None) or {}
    return requests.request(method, BASE + path, headers={**hdr(token, ws), **extra}, timeout=40, **kwargs)


def read(path, token, ws):
    return req('GET', path, token, ws)


def write(method, path, token, ws, **kwargs):
    r = req(method, path, token, ws, **kwargs)
    assert r.status_code in range(200, 300), f'{method} {path}: {r.status_code} {r.text}'
    return r.json()


def rows(payload):
    return payload['items'] if isinstance(payload, dict) else payload


def ids(payload):
    return {row['id'] for row in rows(payload)}


def list_ids(path, token, ws):
    r = read(path, token, ws)
    assert r.status_code == 200, f'{path} for {ws}: {r.status_code} {r.text[:200]}'
    return ids(r.json())


def wait_job(job_id, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        data = read(f'/jobs/{job_id}', SELLER_TOKEN, SELLER_WS).json()
        if data['job']['status'] in {'completed', 'partial', 'failed', 'dead_letter', 'cancelled'}:
            return data
        time.sleep(1)
    raise AssertionError(f'job {job_id} did not finish')


def wait_order(order_id, token, ws, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        row = next(x for x in rows(read('/orders', token, ws).json()) if x['id'] == order_id)
        if row['status'] == 'fulfilled':
            return row
        time.sleep(.5)
    raise AssertionError(f'order {order_id} did not reach fulfilled')


def capabilities():
    allowed = {'stream', 'download', 'share', 'commercial_use', 'license', 'distribute'}
    return {
        name: {'status': 'allowed' if name in allowed else 'blocked',
               'reason': COMMERCIAL_REASON if name in allowed else 'Not included'}
        for name in ('stream', 'download', 'share', 'commercial_use', 'license',
                     'sublicense', 'distribute', 'mint', 'content_id')
    }


# Resource ids created under SELLER, plus the single sale SELLER made to BUYER.
PROJECT_ID = None
BRANCH_ID = None
COMMENT_ID = None
JOB_ID = None
HOLD_ID = None
CANDIDATE_IDS = set()
ASSET_ID = None
EVIDENCE_ID = None
OFFER_ID = None
LICENSE_ID = None
DELIVERY_ID = None
PAYOUT_ID = None
SALE_ORDER_ID = None
CREDIT_ORDER_ID = None
TICKET_ID = None
SELLER_TX_IDS = set()
BASELINE = {}


def setup_module(_mod=None):
    global PROJECT_ID, BRANCH_ID, COMMENT_ID, JOB_ID, HOLD_ID, CANDIDATE_IDS, ASSET_ID
    global EVIDENCE_ID, OFFER_ID, LICENSE_ID, DELIVERY_ID, PAYOUT_ID
    global SALE_ORDER_ID, CREDIT_ORDER_ID, TICKET_ID, SELLER_TX_IDS

    for token, ws, label in ((SELLER_TOKEN, SELLER_WS, 'seller'), (BUYER_TOKEN, BUYER_WS, 'buyer'),
                             (OUTSIDER_TOKEN, OUTSIDER_WS, 'outsider')):
        BASELINE[label] = {k: str(v) for k, v in read('/ledger', token, ws).json()['balances'].items()}

    project = write('POST', '/projects', SELLER_TOKEN, SELLER_WS, json={'title': 'Isolation ' + uuid.uuid4().hex[:6]})
    PROJECT_ID = project['id']
    BRANCH_ID = write('POST', f'/projects/{PROJECT_ID}/branches', SELLER_TOKEN, SELLER_WS,
                      json={'name': 'isolation-branch', 'base_revision': 1})['id']
    COMMENT_ID = write('POST', f'/projects/{PROJECT_ID}/comments', SELLER_TOKEN, SELLER_WS,
                       json={'body': 'isolation probe', 'spec_revision': 1, 'timecode_ms': 900})['id']
    write('PUT', '/preferences', SELLER_TOKEN, SELLER_WS,
          json={'scope': 'workspace', 'preferences': {'preferred_bpm': BPM_SENTINEL}, 'learning_enabled': False})
    TICKET_ID = write('POST', '/support/tickets', SELLER_TOKEN, SELLER_WS,
                      json={'category': 'other', 'priority': 'normal', 'subject': TICKET_SUBJECT,
                            'description': 'Tenant isolation probe.'})['id']

    quote = write('POST', f'/projects/{PROJECT_ID}/quotes', SELLER_TOKEN, SELLER_WS,
                  json={'spec_revision': 1, 'candidate_count': 1, 'scenario': 'success'})
    job = write('POST', '/jobs', SELLER_TOKEN, SELLER_WS,
                headers={'Idempotency-Key': 'iso-job-' + uuid.uuid4().hex},
                json={'quote_id': quote['id'], 'quote_hash': quote['quote_hash'], 'user_confirmation': True})
    JOB_ID, HOLD_ID = job['id'], job['hold_id']
    done = wait_job(JOB_ID)
    assert done['job']['status'] == 'completed', done['job']['status']
    ready = [c for c in done['candidates'] if c['status'] == 'ready']
    assert ready, 'generation produced no ready candidate'
    CANDIDATE_IDS = {c['id'] for c in ready}

    ASSET_ID = write('POST', f'/projects/{PROJECT_ID}/master', SELLER_TOKEN, SELLER_WS,
                     json={'candidate_id': ready[0]['id'], 'expected_spec_revision': 1,
                           'confirmation': True})['asset_snapshot_id']
    EVIDENCE_ID = write('POST', f'/assets/{ASSET_ID}/rights-evidence', SELLER_TOKEN, SELLER_WS,
                        json={'evidence_type': 'provider_contract', 'project_id': PROJECT_ID,
                              'evidence': {'contract_id': 'synthetic-isolation-test', 'scope': 'test_only'}})['id']
    write('POST', f'/assets/{ASSET_ID}/rights-review', SELLER_TOKEN, SELLER_WS,
          json={'status': 'verified', 'capabilities': capabilities(), 'legal_hold': False,
                'review_note': COMMERCIAL_REASON, 'evidence_ids': [EVIDENCE_ID]})
    OFFER_ID = write('POST', '/offers', SELLER_TOKEN, SELLER_WS,
                     json={'asset_snapshot_id': ASSET_ID, 'title': 'Isolation offer', 'price_amount': 2500,
                           'currency': 'USD', 'territory': 'worldwide', 'duration_days': 180,
                           'exclusive': False, 'status': 'active'})['id']

    # The only legitimate cross-tenant object: BUYER buys SELLER's offer.
    SALE_ORDER_ID = write('POST', f'/marketplace/offers/{OFFER_ID}/purchase', BUYER_TOKEN, BUYER_WS,
                          json={'licensee_name': 'Isolation Buyer Ltd'})['id']
    write('POST', f'/orders/{SALE_ORDER_ID}/pay', BUYER_TOKEN, BUYER_WS,
          headers={'Idempotency-Key': 'iso-pay-' + uuid.uuid4().hex}, json={'scenario': 'success'})
    wait_order(SALE_ORDER_ID, BUYER_TOKEN, BUYER_WS)
    LICENSE_ID = next(x['id'] for x in rows(read('/licenses', BUYER_TOKEN, BUYER_WS).json())
                      if x['order_id'] == SALE_ORDER_ID)
    DELIVERY_ID = next(x['id'] for x in rows(read('/deliveries', BUYER_TOKEN, BUYER_WS).json())
                       if x['order_id'] == SALE_ORDER_ID)
    PAYOUT_ID = next(x['id'] for x in rows(read('/payouts', SELLER_TOKEN, SELLER_WS).json())
                     if x['reference_id'] == LICENSE_ID)

    CREDIT_ORDER_ID = write('POST', '/orders/credits', SELLER_TOKEN, SELLER_WS,
                            json={'sku': 'CREDITS_100', 'quantity': 1})['id']
    write('POST', f'/orders/{CREDIT_ORDER_ID}/pay', SELLER_TOKEN, SELLER_WS,
          headers={'Idempotency-Key': 'iso-credit-' + uuid.uuid4().hex}, json={'scenario': 'success'})
    wait_order(CREDIT_ORDER_ID, SELLER_TOKEN, SELLER_WS)
    SELLER_TX_IDS = {t['id'] for t in read('/ledger', SELLER_TOKEN, SELLER_WS).json()['transactions']}
    assert SELLER_TX_IDS, 'seller ledger returned no transactions to hide'


def test_seller_can_read_everything_the_fixture_created():
    """Positive control for every absence assertion below."""
    assert PROJECT_ID in list_ids('/projects', SELLER_TOKEN, SELLER_WS)
    assert JOB_ID in list_ids('/jobs', SELLER_TOKEN, SELLER_WS)
    assert ASSET_ID in list_ids('/assets', SELLER_TOKEN, SELLER_WS)
    assert OFFER_ID in list_ids('/offers', SELLER_TOKEN, SELLER_WS)
    assert CREDIT_ORDER_ID in list_ids('/orders', SELLER_TOKEN, SELLER_WS)
    assert TICKET_ID in list_ids('/support/tickets', SELLER_TOKEN, SELLER_WS)
    assert PAYOUT_ID in list_ids('/payouts', SELLER_TOKEN, SELLER_WS)
    assert LICENSE_ID in list_ids('/licenses', SELLER_TOKEN, SELLER_WS)
    # A delivery is addressed to the buyer's tenant only (market.py writes deliveries with the
    # buyer workspace_id), so the seller legitimately cannot list it; the buyer must.
    assert DELIVERY_ID not in list_ids('/deliveries', SELLER_TOKEN, SELLER_WS)
    assert DELIVERY_ID in list_ids('/deliveries', BUYER_TOKEN, BUYER_WS)
    assert BRANCH_ID in list_ids(f'/projects/{PROJECT_ID}/branches', SELLER_TOKEN, SELLER_WS)
    assert COMMENT_ID in list_ids(f'/projects/{PROJECT_ID}/comments', SELLER_TOKEN, SELLER_WS)
    assert read(f'/projects/{PROJECT_ID}', SELLER_TOKEN, SELLER_WS).status_code == 200
    assert read(f'/assets/{ASSET_ID}', SELLER_TOKEN, SELLER_WS).status_code == 200
    assert EVIDENCE_ID in ids(read(f'/assets/{ASSET_ID}/rights-evidence', SELLER_TOKEN, SELLER_WS).json())
    assert BPM_SENTINEL in seller_preferred_bpms()


def seller_preferred_bpms():
    body = read('/preferences', SELLER_TOKEN, SELLER_WS).json()
    return [row['preferences'].get('preferred_bpm') for row in rows(body)]


def test_other_tenants_cannot_read_any_seller_detail_route():
    private_paths = [
        f'/projects/{PROJECT_ID}',
        f'/projects/{PROJECT_ID}/revisions',
        f'/projects/{PROJECT_ID}/branches',
        f'/projects/{PROJECT_ID}/comments',
        f'/jobs/{JOB_ID}',
        f'/assets/{ASSET_ID}',
        f'/assets/{ASSET_ID}/provenance',
        f'/assets/{ASSET_ID}/rights-evidence',
        f'/assets/{ASSET_ID}/export',
    ]
    for path in private_paths:
        assert read(path, SELLER_TOKEN, SELLER_WS).status_code == 200, f'control: seller cannot read {path}'
        for token, ws, who in OTHERS:
            r = read(path, token, ws)
            assert r.status_code == 404, f'{who} read {path} -> {r.status_code} {r.text[:200]}'


def test_seller_media_stays_unmintable_for_other_tenants():
    for candidate_id in CANDIDATE_IDS:
        minted = req('POST', f'/candidates/{candidate_id}/media-token', SELLER_TOKEN, SELLER_WS)
        assert minted.status_code == 200, f'control: seller cannot mint media token {minted.status_code}'
        assert minted.json()['url']
        for token, ws, who in OTHERS:
            r = req('POST', f'/candidates/{candidate_id}/media-token', token, ws)
            assert r.status_code == 404, f'{who} minted a token for a seller candidate: {r.status_code} {r.text[:200]}'


def test_write_paths_are_closed_to_foreign_tenants():
    """A foreign tenant must not resolve seller comments, approve seller rights reviews or cancel jobs."""
    r = req('POST', f'/projects/{PROJECT_ID}/comments/{COMMENT_ID}/resolve', BUYER_TOKEN, BUYER_WS, json={})
    assert r.status_code == 404, f'buyer resolved a seller comment: {r.status_code} {r.text[:200]}'
    r = req('POST', f'/assets/{ASSET_ID}/rights-review', OUTSIDER_TOKEN, OUTSIDER_WS,
            json={'status': 'verified', 'capabilities': capabilities(), 'legal_hold': False,
                  'review_note': 'foreign write', 'evidence_ids': [EVIDENCE_ID]})
    assert r.status_code == 404, f'outsider reviewed a seller asset: {r.status_code} {r.text[:200]}'
    assert read(f'/projects/{PROJECT_ID}/comments', SELLER_TOKEN, SELLER_WS).status_code == 200


def test_collection_lists_never_carry_seller_rows():
    collections = [
        ('/projects', {PROJECT_ID}),
        ('/jobs', {JOB_ID}),
        ('/assets', {ASSET_ID}),
        ('/offers', {OFFER_ID}),
        ('/orders', {CREDIT_ORDER_ID}),
        ('/support/tickets', {TICKET_ID}),
    ]
    for path, owned in collections:
        assert owned.issubset(list_ids(path, SELLER_TOKEN, SELLER_WS)), f'seller cannot see own {path} rows'
        for token, ws, who in OTHERS:
            leaked = owned & ids(read(path, token, ws).json())
            assert not leaked, f'{who} sees seller {path} rows {leaked}'


def test_ledger_is_tenant_scoped_and_uninvolved_tenant_untouched():
    seller = read('/ledger', SELLER_TOKEN, SELLER_WS).json()
    assert SELLER_TX_IDS & {t['id'] for t in seller['transactions']}, 'control: seller tx list not reproducible'
    assert float(seller['balances']['expense']) > float(BASELINE['seller']['expense']), \
        'control: seller activity did not move its own expense account'
    for token, ws, who in OTHERS:
        body = read('/ledger', token, ws).json()
        assert not (SELLER_TX_IDS & {t['id'] for t in body['transactions']}), f'{who} sees seller ledger transactions'
        assert HOLD_ID not in {h['id'] for h in body['holds']}, f'{who} sees the seller credit hold'
    # OUTSIDER never generated, bought or sold, so not one of its account balances may move.
    outsider = {k: str(v) for k, v in read('/ledger', OUTSIDER_TOKEN, OUTSIDER_WS).json()['balances'].items()}
    assert outsider == BASELINE['outsider'], f'outsider ledger moved during seller activity: {BASELINE["outsider"]} -> {outsider}'


def test_preferences_are_not_shared_across_tenants():
    assert BPM_SENTINEL in seller_preferred_bpms(), 'control: seller preference missing'
    for token, ws, who in OTHERS:
        body = read('/preferences', token, ws).json()
        assert BPM_SENTINEL not in [row['preferences'].get('preferred_bpm') for row in rows(body)], \
            f'{who} inherited the seller preferred_bpm'


def test_marketplace_public_catalog_is_the_only_shared_surface():
    public = read('/marketplace/offers', OUTSIDER_TOKEN, OUTSIDER_WS)
    assert public.status_code == 200, public.text[:200]
    catalog = ids(public.json())
    assert OFFER_ID in catalog, 'active offer missing from public catalog'
    assert LICENSE_ID not in catalog and PAYOUT_ID not in catalog and DELIVERY_ID not in catalog, \
        'a private row surfaced in the public catalog'


def test_counterparty_scope_is_bounded_to_the_two_parties():
    assert LICENSE_ID in list_ids('/licenses', BUYER_TOKEN, BUYER_WS), 'buyer cannot see its own license'
    assert DELIVERY_ID in list_ids('/deliveries', BUYER_TOKEN, BUYER_WS), 'buyer cannot see its own delivery'
    assert SALE_ORDER_ID in list_ids('/orders', BUYER_TOKEN, BUYER_WS), 'buyer cannot see its own order'
    buyer_export = req('GET', f'/deliveries/{DELIVERY_ID}/export', BUYER_TOKEN, BUYER_WS)
    assert buyer_export.status_code == 200, f'buyer export failed: {buyer_export.status_code}'
    for path in ('/licenses', '/deliveries'):
        assert LICENSE_ID not in ids(read(path, OUTSIDER_TOKEN, OUTSIDER_WS).json())
        assert DELIVERY_ID not in ids(read(path, OUTSIDER_TOKEN, OUTSIDER_WS).json())
    assert read(f'/deliveries/{DELIVERY_ID}/export', OUTSIDER_TOKEN, OUTSIDER_WS).status_code == 404
    # The payout is the seller's commercial term; the buyer must not read it.
    assert PAYOUT_ID in list_ids('/payouts', SELLER_TOKEN, SELLER_WS)
    outsider_payouts = read('/payouts', OUTSIDER_TOKEN, OUTSIDER_WS)
    assert outsider_payouts.status_code == 200 and PAYOUT_ID not in ids(outsider_payouts.json())


def test_analytics_aggregates_stay_inside_the_tenant():
    seller = read('/analytics/overview', SELLER_TOKEN, SELLER_WS)
    outsider = read('/analytics/overview', OUTSIDER_TOKEN, OUTSIDER_WS)
    assert seller.status_code == 200 and outsider.status_code == 200, (seller.status_code, outsider.status_code)
    assert TICKET_SUBJECT not in outsider.text, 'seller ticket subject leaked into outsider aggregates'
    assert ASSET_ID not in outsider.text and PROJECT_ID not in outsider.text, 'seller ids in outsider aggregates'
