import os, sys, json, io
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '.')
os.environ['SECRET_NAME'] = ''

def load_env():
    with open('.env') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())
load_env()

from lambda_function import handler

passed = failed = 0

def ok(label):
    global passed; passed += 1
    print(f'  PASS  {label}')

def fail(label, reason=''):
    global failed; failed += 1
    print(f'  FAIL  {label}' + (f': {reason}' if reason else ''))

def call(method, path, body=None, token=None, params=None):
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    event = {
        'httpMethod': method, 'path': path,
        'queryStringParameters': params,
        'headers': headers,
        'body': json.dumps(body) if body else None,
        'isBase64Encoded': False, 'requestContext': {}
    }
    r = handler(event, {})
    return r['statusCode'], json.loads(r['body'])

# ── 1. DB TABLES ──────────────────────────────────────────────────────
print('\n=== 1. DATABASE TABLES ===')
from utilities.db_connection import get_engine, metadata
get_engine()
expected = [
    'users','user_addresses','refresh_tokens','token_connections',
    'categories','services','sub_categories','sub_services',
    'providers','provider_services','provider_documents','provider_locations',
    'bookings','booking_items','instant_bookings',
    'payments','tips','reviews','coupon_uses','coupons',
    'subscription_plans','user_subscriptions',
    'provider_earnings','payout_requests',
    'support_tickets','ticket_messages',
    'in_app_notifications','activity_log',
    'chat_messages','ws_connections',
]
for t in expected:
    if t in metadata.tables:
        ok(f'Table: {t}')
    else:
        fail(f'Table: {t}', 'MISSING')

# ── 2. REDIS ──────────────────────────────────────────────────────────
print('\n=== 2. REDIS ===')
from utilities.redis_connection import get_redis
r = get_redis()
r.setex('__test__', 5, 'ok')
ok('Redis set/get') if r.get('__test__') == 'ok' else fail('Redis set/get')
r.incr('__ctr__'); r.incr('__ctr__')
ok('Redis incr') if int(r.get('__ctr__') or 0) >= 2 else fail('Redis incr')
r.delete('__test__', '__ctr__')

# ── 3. AUTH ───────────────────────────────────────────────────────────
print('\n=== 3. AUTH FLOW ===')
PHONE = '9876543210'
buf = io.StringIO()
with redirect_stdout(buf):
    sc, res = call('POST', '/auth/send-otp', {'phone': PHONE})
otp_lines = [l for l in buf.getvalue().splitlines() if 'OTP for' in l]
if sc == 200 and otp_lines:
    otp = otp_lines[0].split(': ')[1].strip()
    ok(f'send-otp (OTP={otp})')
else:
    fail('send-otp', str(res)); otp = None

token = refresh = user_id = None
if otp:
    sc, res = call('POST', '/auth/verify-otp', {'phone': PHONE, 'otp': otp})
    if sc == 200 and res['data'].get('access_token'):
        token = res['data']['access_token']
        refresh = res['data']['refresh_token']
        user_id = res['data']['user']['user_id']
        ok(f'verify-otp -> user_id={user_id[:8]}... is_new={res["data"]["is_new_user"]}')
    else:
        fail('verify-otp', str(res))

if token:
    sc, res = call('GET', '/auth/me', token=token)
    ok('GET /auth/me') if sc == 200 and res['data']['phone'] == PHONE else fail('GET /auth/me', str(res))

    sc, res = call('PATCH', '/auth/me', {'name': 'Test Customer', 'email': 'test@7star.com'}, token=token)
    ok('PATCH /auth/me (name+email)') if sc == 200 else fail('PATCH /auth/me', str(res))

if refresh:
    sc, res = call('POST', '/auth/refresh', {'refresh_token': refresh})
    if sc == 200 and res['data'].get('access_token'):
        token = res['data']['access_token']
        ok('POST /auth/refresh -> new token')
    else:
        fail('POST /auth/refresh', str(res))

# ── 4. OTP RATE LIMIT ─────────────────────────────────────────────────
print('\n=== 4. OTP RATE LIMIT ===')
for i in range(5):
    with redirect_stdout(io.StringIO()):
        call('POST', '/auth/send-otp', {'phone': '9000000099'})
sc, res = call('POST', '/auth/send-otp', {'phone': '9000000099'})
ok('Rate limit blocks 6th OTP') if sc == 400 else fail('Rate limit', str(res))

# ── 5. SERVICES CATALOG ───────────────────────────────────────────────
print('\n=== 5. SERVICES CATALOG ===')
sc, res = call('GET', '/categories', token=token)
ok(f'GET /categories -> {len(res.get("data",[]))} categories') if sc == 200 and len(res.get('data',[])) == 9 else fail('GET /categories', f'got {len(res.get("data",[]))}')

sc, res = call('GET', '/services', token=token)
ok(f'GET /services -> {len(res.get("data",[]))} services') if sc == 200 and len(res.get('data',[])) == 9 else fail('GET /services', f'got {len(res.get("data",[]))}')

sc, res = call('GET', '/services/s1', token=token)
ok('GET /services/s1 (detail)') if sc == 200 else fail('GET /services/s1', str(res))

sc, res = call('GET', '/services/search', token=token, params={'q': 'clean'})
ok(f'GET /services/search -> {len(res.get("data",[]))} results') if sc == 200 else fail('GET /services/search', str(res))

# ── 6. SUBSCRIPTIONS ──────────────────────────────────────────────────
print('\n=== 6. SUBSCRIPTIONS ===')
sc, res = call('GET', '/subscriptions/plans', token=token)
ok(f'GET /subscriptions/plans -> {len(res.get("data",[]))} plans') if sc == 200 and len(res.get('data',[])) == 3 else fail('GET /subscriptions/plans', str(res))

sc, res = call('GET', '/subscriptions/mine', token=token)
ok('GET /subscriptions/mine') if sc == 200 else fail('GET /subscriptions/mine', str(res))

# ── 7. COUPONS ────────────────────────────────────────────────────────
print('\n=== 7. COUPONS ===')
sc, res = call('GET', '/coupons', token=token)
ok(f'GET /coupons -> {len(res.get("data",[]))} coupons') if sc == 200 else fail('GET /coupons', str(res))

# ── 8. PROVIDERS ──────────────────────────────────────────────────────
print('\n=== 8. PROVIDERS ===')
sc, res = call('GET', '/providers/nearby', token=token, params={'lat': '17.385', 'lng': '78.486', 'service_id': 's1'})
ok(f'GET /providers/nearby -> {len(res.get("data",[]))} nearby') if sc == 200 else fail('GET /providers/nearby', str(res))

# ── 9. NOTIFICATIONS ──────────────────────────────────────────────────
print('\n=== 9. NOTIFICATIONS ===')
sc, res = call('POST', '/notifications/register', {'token_id': 'test-fcm-xyz', 'device_type': 'android'}, token=token)
ok('POST /notifications/register') if sc in (200, 201) else fail('POST /notifications/register', str(res))

sc, res = call('GET', '/notifications', token=token)
ok(f'GET /notifications -> {len(res.get("data",[]))} items') if sc == 200 else fail('GET /notifications', str(res))

# ── 10. BOOKINGS ──────────────────────────────────────────────────────
print('\n=== 10. BOOKINGS ===')
sc, res = call('GET', '/bookings', token=token)
ok(f'GET /bookings -> {len(res.get("data",[]))} bookings') if sc == 200 else fail('GET /bookings', str(res))

future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
sc, res = call('POST', '/bookings', {
    'service_id': 's3',
    'scheduled_at': future,
    'sub_total': 49900,
    'total_amount': 49900,
    'address_snapshot': {'lat': 17.385, 'lng': 78.486, 'full_address': '123 Test St, Hyderabad'}
}, token=token)
booking_id = None
if sc == 201:
    booking_id = res['data']['booking_id']
    ok(f'POST /bookings -> created {booking_id[:8]}...')
else:
    fail('POST /bookings', str(res))

if booking_id:
    sc, res = call('GET', f'/bookings/{booking_id}', token=token)
    ok('GET /bookings/{id}') if sc == 200 else fail('GET /bookings/{id}', str(res))

    sc, res = call('POST', f'/bookings/{booking_id}/cancel', {}, token=token)
    ok('POST /bookings/{id}/cancel') if sc == 200 else fail('POST /bookings/{id}/cancel', str(res))

# ── 11. SUPPORT TICKETS ───────────────────────────────────────────────
print('\n=== 11. SUPPORT ===')
sc, res = call('POST', '/support/tickets', {
    'subject': 'Test issue',
    'category': 'general',
}, token=token)
ticket_id = None
if sc == 201:
    ticket_id = res['data']['ticket_id']
    ok(f'POST /support/tickets -> {ticket_id[:8]}...')
else:
    fail('POST /support/tickets', str(res))

sc, res = call('GET', '/support/tickets', token=token)
ok(f'GET /support/tickets -> {len(res.get("data",[]))} tickets') if sc == 200 else fail('GET /support/tickets', str(res))

# ── 12. LOGOUT ────────────────────────────────────────────────────────
print('\n=== 12. LOGOUT ===')
sc, res = call('POST', '/auth/logout', {'refresh_token': refresh}, token=token)
ok('POST /auth/logout') if sc == 200 else fail('POST /auth/logout', str(res))

# verify token is revoked
sc, res = call('POST', '/auth/refresh', {'refresh_token': refresh})
ok('Refresh token revoked after logout') if sc == 403 else fail('Token revoke check', str(res))

# ── SUMMARY ───────────────────────────────────────────────────────────
print(f'\n{"="*50}')
print(f'Results: {passed} passed, {failed} failed out of {passed+failed}')
print('ALL SYSTEMS GO - ready to push!' if failed == 0 else 'SOME FAILURES - review above')
