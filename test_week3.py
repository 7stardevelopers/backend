import sys, os, jwt
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

os.environ['JWT_SECRET'] = 'test-secret'
os.environ['DB_URL'] = 'placeholder'

passed = failed = 0

def ok(label):
    global passed
    passed += 1
    print(f'  PASS  {label}')

def fail(label, reason=''):
    global failed
    failed += 1
    print(f'  FAIL  {label}' + (f': {reason}' if reason else ''))

with patch('utilities.db_connection.get_engine'):

    # =========================================================
    # Day 1 — WebSocket routing & role propagation
    # =========================================================
    from routing_wss import WSS_ROUTES
    for key in ['$connect', '$disconnect', 'sendMessage', 'locationUpdate']:
        if key in WSS_ROUTES:
            ok(f'WSS route exists: {key}')
        else:
            fail(f'WSS route exists: {key}', 'missing')

    from web_sockets.web_sockets_service import WebSocketsService, _extract_user_from_token

    # _extract_user_from_token returns (user_id, role) tuple
    token = jwt.encode({'user_id': 'u1', 'role': 'PROVIDER'}, 'test-secret', algorithm='HS256')
    uid, role = _extract_user_from_token(token)
    if uid == 'u1' and role == 'PROVIDER':
        ok('_extract_user_from_token returns (user_id, role)')
    else:
        fail('_extract_user_from_token returns (user_id, role)', f'got uid={uid} role={role}')

    # Invalid/empty token returns (None, None)
    r = _extract_user_from_token('')
    if r == (None, None):
        ok('_extract_user_from_token empty token -> (None, None)')
    else:
        fail('_extract_user_from_token empty token', f'got {r}')

    # on_connect rejects missing token
    svc_ws = WebSocketsService()
    with patch.object(svc_ws.modal, 'connect') as mock_conn_ws:
        status, msg = svc_ws.on_connect('conn1', {'queryStringParameters': {}}, MagicMock())
        if status == 'error':
            ok('on_connect rejects missing token')
        else:
            fail('on_connect rejects missing token', f'status={status}')

    # on_connect stores role from JWT
    with patch.object(svc_ws.modal, 'connect') as mock_conn_ws:
        event = {'queryStringParameters': {'token': token}}
        status, _ = svc_ws.on_connect('conn1', event, MagicMock())
        if status == 'success':
            call_kwargs = mock_conn_ws.call_args
            stored_role = call_kwargs[0][4] if call_kwargs[0] else call_kwargs[1].get('role')
            if stored_role == 'PROVIDER':
                ok('on_connect stores role from JWT')
            else:
                fail('on_connect stores role from JWT', f'role={stored_role}')
        else:
            fail('on_connect stores role from JWT', f'status={status}')

    # on_message uses role from ws_record, not hardcoded CUSTOMER
    ws_record = {'user_id': 'u1', 'role': 'PROVIDER', 'booking_id': 'b1'}
    with patch.object(svc_ws.modal, 'get_connection', return_value=ws_record):
        from chat.messages_service import MessagesService
        with patch.object(MessagesService, 'send_message', return_value=('success', {})) as mock_send:
            svc_ws.on_message('conn1', {'body': '{"text":"hi","booking_id":"b1"}'}, MagicMock())
            sent_obj = mock_send.call_args[0][0]
            if sent_obj.get('_role') == 'PROVIDER':
                ok('on_message passes role from ws_record')
            else:
                fail('on_message passes role from ws_record', f'_role={sent_obj.get("_role")}')

    # =========================================================
    # Day 3 — Coupons: validate logic
    # =========================================================
    from coupons.coupons_service import CouponsService, _calculate_discount

    # _calculate_discount FLAT
    coupon_flat = {'type': 'FLAT', 'value': 200, 'max_discount': None}
    assert _calculate_discount(coupon_flat, 1000) == 200
    ok('_calculate_discount FLAT')

    # _calculate_discount PERCENT with cap
    coupon_pct = {'type': 'PERCENT', 'value': 20, 'max_discount': 150}
    assert _calculate_discount(coupon_pct, 1000) == 150   # 20% of 1000=200, capped at 150
    ok('_calculate_discount PERCENT capped')

    coupon_svc = CouponsService()

    # validate: valid coupon
    future_ts = datetime.now(timezone.utc) + timedelta(days=5)
    valid_coupon = {
        'coupon_id': 'cp1', 'code': 'SAVE10', 'title': '10% off',
        'expires_at': future_ts, 'used_count': 0, 'max_uses': 100,
        'min_order_amount': 500, 'service_ids': None,
        'type': 'FLAT', 'value': 100, 'max_discount': None,
    }
    with patch.object(coupon_svc.modal, 'find_by_code', return_value=valid_coupon), \
         patch.object(coupon_svc.modal, 'user_already_used', return_value=False):
        status, result = coupon_svc.validate(
            {'_user_id': 'u1', 'coupon_code': 'SAVE10', 'cart_total': 1000, 'service_id': 's1'},
            MagicMock()
        )
        if status == 'success' and result.get('discount') == 100:
            ok('validate: valid coupon returns discount')
        else:
            fail('validate: valid coupon returns discount', f'status={status} result={result}')

    # validate: expired coupon (naive datetime — simulates MySQL without tz)
    past_naive = datetime.utcnow() - timedelta(days=1)
    expired_coupon = dict(valid_coupon, expires_at=past_naive)
    with patch.object(coupon_svc.modal, 'find_by_code', return_value=expired_coupon):
        try:
            coupon_svc.validate(
                {'_user_id': 'u1', 'coupon_code': 'SAVE10', 'cart_total': 1000, 'service_id': 's1'},
                MagicMock()
            )
            fail('validate: expired naive coupon raises ValueError')
        except ValueError as e:
            if 'expired' in str(e).lower():
                ok('validate: expired naive coupon raises ValueError')
            else:
                fail('validate: expired naive coupon', f'wrong error: {e}')

    # validate: expired coupon (tz-aware datetime — simulates MySQL with tz)
    past_aware = datetime.now(timezone.utc) - timedelta(days=1)
    expired_aware_coupon = dict(valid_coupon, expires_at=past_aware)
    with patch.object(coupon_svc.modal, 'find_by_code', return_value=expired_aware_coupon):
        try:
            coupon_svc.validate(
                {'_user_id': 'u1', 'coupon_code': 'SAVE10', 'cart_total': 1000, 'service_id': 's1'},
                MagicMock()
            )
            fail('validate: expired tz-aware coupon raises ValueError (no crash)')
        except ValueError as e:
            if 'expired' in str(e).lower():
                ok('validate: expired tz-aware coupon handled without crash')
            else:
                fail('validate: expired tz-aware coupon', f'wrong error: {e}')
        except TypeError as e:
            fail('validate: tz-aware expires_at crashed with TypeError', str(e))

    # validate: already used
    with patch.object(coupon_svc.modal, 'find_by_code', return_value=valid_coupon), \
         patch.object(coupon_svc.modal, 'user_already_used', return_value=True):
        try:
            coupon_svc.validate(
                {'_user_id': 'u1', 'coupon_code': 'SAVE10', 'cart_total': 1000, 'service_id': 's1'},
                MagicMock()
            )
            fail('validate: already-used coupon raises ValueError')
        except ValueError as e:
            ok('validate: already-used coupon raises ValueError')

    # validate: cart below minimum
    with patch.object(coupon_svc.modal, 'find_by_code', return_value=valid_coupon), \
         patch.object(coupon_svc.modal, 'user_already_used', return_value=False):
        try:
            coupon_svc.validate(
                {'_user_id': 'u1', 'coupon_code': 'SAVE10', 'cart_total': 400, 'service_id': 's1'},
                MagicMock()
            )
            fail('validate: cart below min raises ValueError')
        except ValueError:
            ok('validate: cart below minimum raises ValueError')

    # coupons_modal: no local imports inside class methods
    import inspect
    from coupons.coupons_modal import CouponsMaster
    src = inspect.getsource(CouponsMaster)
    if 'from datetime import' not in src:
        ok('coupons_modal: no local datetime import in class')
    else:
        fail('coupons_modal: local datetime import still in class')
    if 'from sqlalchemy import' not in src:
        ok('coupons_modal: no local sqlalchemy import in class')
    else:
        fail('coupons_modal: local sqlalchemy import still in class')

    # =========================================================
    # Day 3+4 — bookings.create: coupon record_use + subscription discount
    # =========================================================
    from bookings.bookings_service import BookingsService
    from datetime import datetime, timezone, timedelta

    books_svc = BookingsService()
    future_dt = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    mock_booking = {
        'booking_id': 'bk1', 'customer_id': 'u1', 'status': 'PENDING',
        'sub_total': 1000, 'provider_id': None,
    }

    # bookings.create calls record_use when coupon_id present
    from coupons.coupons_modal import CouponsMaster as CM
    with patch.object(books_svc.modal, 'create', return_value=mock_booking), \
         patch.object(books_svc.modal, 'create_items'), \
         patch.object(books_svc.modal, 'assign_provider'), \
         patch('bookings.bookings_service.match_provider', return_value=None, create=True), \
         patch('bookings.bookings_service.SubscriptionsMaster') as MockSubModal, \
         patch('bookings.bookings_service.CouponsMaster') as MockCM:

        mock_sub_inst = MockSubModal.return_value
        mock_sub_inst.get_active_subscription.return_value = None

        mock_cm_inst = MockCM.return_value
        mock_cm_inst.record_use = MagicMock()

        books_svc.notif = MagicMock()

        try:
            books_svc.create({
                '_user_id': 'u1', '_role': 'CUSTOMER',
                'service_id': 'svc1', 'scheduled_at': future_dt,
                'sub_total': 1000, 'discount': 0, 'total_amount': 1000,
                'coupon_id': 'cp1',
            }, MagicMock())
            if mock_cm_inst.record_use.called:
                ok('bookings.create calls coupon record_use when coupon_id present')
            else:
                fail('bookings.create calls coupon record_use', 'record_use not called')
        except Exception as e:
            fail('bookings.create calls coupon record_use', str(e))

    # bookings.create applies subscription discount
    mock_plan = {'plan_id': 'plan1', 'discount_pct': 10}
    mock_sub = {'subscription_id': 'sub1', 'plan_id': 'plan1', 'status': 'ACTIVE'}
    with patch.object(books_svc.modal, 'create') as mock_create, \
         patch.object(books_svc.modal, 'create_items'), \
         patch('bookings.bookings_service.match_provider', return_value=None, create=True), \
         patch('bookings.bookings_service.SubscriptionsMaster') as MockSubModal2:

        mock_sub_inst2 = MockSubModal2.return_value
        mock_sub_inst2.get_active_subscription.return_value = mock_sub
        mock_sub_inst2.get_plan.return_value = mock_plan
        mock_sub_inst2.increment_bookings_used = MagicMock()

        mock_create.return_value = mock_booking
        books_svc.notif = MagicMock()

        try:
            books_svc.create({
                '_user_id': 'u1', '_role': 'CUSTOMER',
                'service_id': 'svc1', 'scheduled_at': future_dt,
                'sub_total': 1000, 'discount': 0, 'total_amount': 1000,
            }, MagicMock())
            create_call = mock_create.call_args[0][1]
            sub_discount_applied = create_call.get('discount', 0) >= 100  # 10% of 1000
            total_reduced = create_call.get('total_amount', 1000) <= 900
            if sub_discount_applied and total_reduced:
                ok('bookings.create applies subscription discount')
            else:
                fail('bookings.create applies subscription discount',
                     f"discount={create_call.get('discount')} total={create_call.get('total_amount')}")
        except Exception as e:
            fail('bookings.create applies subscription discount', str(e))

    # bookings.create increments bookings_used when sub active
    with patch.object(books_svc.modal, 'create', return_value=mock_booking), \
         patch.object(books_svc.modal, 'create_items'), \
         patch('bookings.bookings_service.match_provider', return_value=None, create=True), \
         patch('bookings.bookings_service.SubscriptionsMaster') as MockSubModal3:

        mock_sub_inst3 = MockSubModal3.return_value
        mock_sub_inst3.get_active_subscription.return_value = mock_sub
        mock_sub_inst3.get_plan.return_value = mock_plan
        mock_sub_inst3.increment_bookings_used = MagicMock()

        books_svc.notif = MagicMock()

        try:
            books_svc.create({
                '_user_id': 'u1', '_role': 'CUSTOMER',
                'service_id': 'svc1', 'scheduled_at': future_dt,
                'sub_total': 1000, 'discount': 0, 'total_amount': 1000,
            }, MagicMock())
            if mock_sub_inst3.increment_bookings_used.called:
                ok('bookings.create increments bookings_used')
            else:
                fail('bookings.create increments bookings_used', 'not called')
        except Exception as e:
            fail('bookings.create increments bookings_used', str(e))

    # =========================================================
    # Day 4 — SubscribeSchema.payment_id is Optional
    # =========================================================
    from subscriptions.subscriptions_validator import SubscribeSchema

    try:
        s = SubscribeSchema(plan_id='plan1')
        if s.payment_id is None:
            ok('SubscribeSchema.payment_id is Optional (no payment_id works)')
        else:
            fail('SubscribeSchema.payment_id is Optional', f'expected None, got {s.payment_id}')
    except Exception as e:
        fail('SubscribeSchema.payment_id is Optional', str(e))

    try:
        s2 = SubscribeSchema(plan_id='plan1', payment_id='pay_abc')
        if s2.payment_id == 'pay_abc':
            ok('SubscribeSchema.payment_id accepts value when provided')
        else:
            fail('SubscribeSchema.payment_id accepts value', f'got {s2.payment_id}')
    except Exception as e:
        fail('SubscribeSchema.payment_id accepts value', str(e))

    # =========================================================
    # Day 5 — Route groups present
    # =========================================================
    from routing import ROUTES
    route_paths = [str(p) for _, p, _, _ in ROUTES]
    for group in ['/coupons', '/subscriptions', '/reviews', '/notifications']:
        if any(group in r for r in route_paths):
            ok(f'Route group present: {group}')
        else:
            fail(f'Route group present: {group}', 'no routes found')

    from routing_wss import WSS_ROUTES
    for key in ['$connect', '$disconnect', 'sendMessage', 'locationUpdate']:
        if key in WSS_ROUTES:
            ok(f'WSS route: {key}')
        else:
            fail(f'WSS route: {key}', 'missing')

# ─── Summary ─────────────────────────────────────────────────
print()
print(f'Results: {passed} passed, {failed} failed out of {passed + failed} checks')
if failed == 0:
    print('Week 3: ALL PASS')
else:
    print('Week 3: SOME FAILURES - review above')
