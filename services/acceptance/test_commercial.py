import io
import time
import uuid
import zipfile

import requests

ROOT = __import__('os').getenv('API_BASE_URL', 'http://localhost:8000')
BASE = ROOT + '/api'


def login(email, password):
    r = requests.post(BASE + '/auth/login', json={'email': email, 'password': password}, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    return data['access_token'], data['workspaces'][0]['id']


SELLER_TOKEN, SELLER_WS = login('owner@example.local', 'demo-owner')
BUYER_TOKEN, BUYER_WS = login('viewer@other.local', 'demo-viewer')


def h(token, ws, extra=None):
    return {'Authorization': 'Bearer ' + token, 'X-Workspace-Id': ws, 'Content-Type': 'application/json', **(extra or {})}


def call(method, path, token, ws, ok=range(200, 400), **kwargs):
    r = requests.request(method, BASE + path, headers={**h(token, ws), **kwargs.pop('headers', {})}, timeout=40, **kwargs)
    assert r.status_code in ok, f'{method} {path}: {r.status_code} {r.text}'
    return r


def wait_job(job_id, timeout=80):
    end = time.time() + timeout
    while time.time() < end:
        data = call('GET', f'/jobs/{job_id}', SELLER_TOKEN, SELLER_WS).json()
        if data['job']['status'] in {'completed', 'partial', 'failed', 'dead_letter', 'cancelled'}:
            return data
        time.sleep(1)
    raise AssertionError('job timeout')


def wait_order(order_id, status, timeout=35):
    end = time.time() + timeout
    while time.time() < end:
        rows = call('GET', '/orders', BUYER_TOKEN, BUYER_WS).json()['items']
        row = next(x for x in rows if x['id'] == order_id)
        if row['status'] == status:
            return row
        time.sleep(.5)
    raise AssertionError(f'order did not reach {status}')


def test_synthetic_commercial_license_delivery_and_reversal():
    project = call('POST', '/projects', SELLER_TOKEN, SELLER_WS, json={'title': 'Commercial acceptance ' + uuid.uuid4().hex[:6]}).json()
    quote = call('POST', f"/projects/{project['id']}/quotes", SELLER_TOKEN, SELLER_WS, json={'candidate_count': 1, 'scenario': 'success'}).json()
    job = call('POST', '/jobs', SELLER_TOKEN, SELLER_WS, headers={'Idempotency-Key': 'commercial-' + uuid.uuid4().hex}, json={'quote_id': quote['id'], 'quote_hash': quote['quote_hash'], 'user_confirmation': True}).json()
    done = wait_job(job['id'])
    candidate = next(x for x in done['candidates'] if x['status'] == 'ready')
    asset = call('POST', f"/projects/{project['id']}/master", SELLER_TOKEN, SELLER_WS, json={'candidate_id': candidate['id'], 'expected_spec_revision': 1, 'confirmation': True}).json()
    capabilities = {
        'stream': {'status': 'allowed', 'reason': 'Synthetic approved contract test'},
        'download': {'status': 'allowed', 'reason': 'Synthetic approved contract test'},
        'share': {'status': 'allowed', 'reason': 'Synthetic approved contract test'},
        'commercial_use': {'status': 'allowed', 'reason': 'Synthetic approved contract test'},
        'license': {'status': 'allowed', 'reason': 'Synthetic approved contract test'},
        'sublicense': {'status': 'blocked', 'reason': 'Not included'},
        'distribute': {'status': 'allowed', 'reason': 'Synthetic approved contract test'},
        'mint': {'status': 'blocked', 'reason': 'Not included'},
        'content_id': {'status': 'blocked', 'reason': 'Not included'},
    }
    evidence = call('POST', f"/assets/{asset['asset_snapshot_id']}/rights-evidence", SELLER_TOKEN, SELLER_WS, json={
        'evidence_type': 'provider_contract',
        'project_id': project['id'],
        'evidence': {'contract_id': 'synthetic-commercial-contract-test', 'scope': 'test_only', 'provider': 'synthetic_licensed'}
    }).json()
    call('POST', f"/assets/{asset['asset_snapshot_id']}/rights-review", SELLER_TOKEN, SELLER_WS, json={'status': 'verified', 'capabilities': capabilities, 'legal_hold': False, 'review_note': 'Synthetic contract test only', 'evidence_ids': [evidence['id']]})
    offer = call('POST', '/offers', SELLER_TOKEN, SELLER_WS, json={'asset_snapshot_id': asset['asset_snapshot_id'], 'title': 'Exclusive commercial test', 'price_amount': 4900, 'currency': 'USD', 'territory': 'worldwide', 'duration_days': 365, 'exclusive': True, 'status': 'active'}).json()
    order = call('POST', f"/marketplace/offers/{offer['id']}/purchase", BUYER_TOKEN, BUYER_WS, json={'licensee_name': 'Other Workspace'}).json()
    hidden = call('GET', '/marketplace/offers', SELLER_TOKEN, SELLER_WS).json()['items']
    assert all(x['id'] != offer['id'] for x in hidden), 'exclusive reserved offer must be hidden'
    pay_key = 'license-pay-' + uuid.uuid4().hex
    call('POST', f"/orders/{order['id']}/pay", BUYER_TOKEN, BUYER_WS, headers={'Idempotency-Key': pay_key}, json={'scenario': 'success'})
    wait_order(order['id'], 'fulfilled')
    licenses = call('GET', '/licenses', BUYER_TOKEN, BUYER_WS).json()['items']
    license_row = next(x for x in licenses if x['order_id'] == order['id'])
    assert license_row['status'] == 'active'
    deliveries = call('GET', '/deliveries', BUYER_TOKEN, BUYER_WS).json()['items']
    delivery = next(x for x in deliveries if x['order_id'] == order['id'])
    exported = call('GET', f"/deliveries/{delivery['id']}/export", BUYER_TOKEN, BUYER_WS)
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    assert {'license/license.json', 'license/rights-manifest.json', 'asset/asset-snapshot.json'}.issubset(archive.namelist())
    assert any(name.startswith('media/master') for name in archive.namelist())
    payouts = call('GET', '/payouts', SELLER_TOKEN, SELLER_WS).json()['items']
    payout = next(x for x in payouts if x['reference_id'] == license_row['id'])
    assert payout['amount'] == 4165 and payout['status'] == 'pending'
    call('POST', f"/orders/{order['id']}/refunds", BUYER_TOKEN, BUYER_WS, headers={'Idempotency-Key': 'license-refund-' + uuid.uuid4().hex}, json={'reason': 'Commercial acceptance reversal'})
    wait_order(order['id'], 'refunded')
    deliveries = call('GET', '/deliveries', BUYER_TOKEN, BUYER_WS).json()['items']
    assert next(x for x in deliveries if x['id'] == delivery['id'])['status'] == 'revoked'
    payouts = call('GET', '/payouts', SELLER_TOKEN, SELLER_WS).json()['items']
    assert next(x for x in payouts if x['id'] == payout['id'])['status'] == 'reversed'

    # Brand brief award uses the same immutable asset/rights evidence, but creates a
    # separate order, license, delivery and seller payout.
    brief = call('POST', '/brand-briefs', BUYER_TOKEN, BUYER_WS, json={
        'title': 'Brand campaign contract test', 'description': 'A complete paid brand award flow.',
        'budget_amount': 3000, 'currency': 'USD', 'requirements': {'usage': 'social campaign'}, 'status': 'open'
    }).json()
    submission = call('POST', f"/brand-briefs/{brief['id']}/submissions", SELLER_TOKEN, SELLER_WS, json={
        'asset_snapshot_id': asset['asset_snapshot_id'], 'notes': 'Approved synthetic asset for contract testing.'
    }).json()
    award_order = call('POST', f"/brand-submissions/{submission['id']}/award", BUYER_TOKEN, BUYER_WS, json={
        'licensee_name': 'Other Workspace Brand', 'territory': 'worldwide', 'duration_days': 180
    }).json()
    call('POST', f"/orders/{award_order['id']}/pay", BUYER_TOKEN, BUYER_WS, headers={'Idempotency-Key': 'brand-pay-' + uuid.uuid4().hex}, json={'scenario': 'success'})
    wait_order(award_order['id'], 'fulfilled')
    brand_license = next(x for x in call('GET', '/licenses', BUYER_TOKEN, BUYER_WS).json()['items'] if x['order_id'] == award_order['id'])
    assert brand_license['status'] == 'active'
    brand_delivery = next(x for x in call('GET', '/deliveries', BUYER_TOKEN, BUYER_WS).json()['items'] if x['order_id'] == award_order['id'])
    brand_zip = call('GET', f"/deliveries/{brand_delivery['id']}/export", BUYER_TOKEN, BUYER_WS)
    assert zipfile.is_zipfile(io.BytesIO(brand_zip.content))
    brand_payout = next(x for x in call('GET', '/payouts', SELLER_TOKEN, SELLER_WS).json()['items'] if x['reference_id'] == brand_license['id'])
    assert brand_payout['amount'] == 2550 and brand_payout['status'] == 'pending'
    call('POST', f"/orders/{award_order['id']}/refunds", BUYER_TOKEN, BUYER_WS, headers={'Idempotency-Key': 'brand-refund-' + uuid.uuid4().hex}, json={'reason': 'Brand award contract reversal'})
    wait_order(award_order['id'], 'refunded')
    assert next(x for x in call('GET', '/payouts', SELLER_TOKEN, SELLER_WS).json()['items'] if x['id'] == brand_payout['id'])['status'] == 'reversed'
