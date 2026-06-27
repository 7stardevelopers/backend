import sys, os
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from unittest.mock import patch, MagicMock
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['DB_URL'] = 'placeholder'
os.environ['RAZORPAY_KEY_ID'] = 'rzp_test_key'
os.environ['RAZORPAY_KEY_SECRET'] = 'rzp_test_secret'

with patch('utilities.db_connection.get_engine'):

    # ─── Day 1: Validators ───────────────────────────────────────────────
    from datetime import datetime, timezone, timedelta
    from bookings.bookings_validator import CreateBookingSchema

    future = datetime.now(timezone.utc) + timedelta(days=1)
    past   = datetime.now(timezone.utc) - timedelta(days=1)
    CreateBookingSchema(service_id='svc1', scheduled_at=future, sub_total=1000, total_amount=1000)
    print('Day 1 - CreateBookingSchema future date: PASS')

    try:
        CreateBookingSchema(service_id='svc1', scheduled_at=past, sub_total=1000, total_amount=1000)
        print('Day 1 - Past date rejection: FAIL')
    except Exception:
        print('Day 1 - Past date rejection: PASS')

    from payments.payment_validator import CreateOrderSchema
    assert CreateOrderSchema(booking_id='b1').amount is None
    print('Day 1 - CreateOrderSchema amount is Optional: PASS')

    # ─── Day 2: State machine + guards ──────────────────────────────────
    from bookings.bookings_service import ALLOWED_TRANSITIONS, BookingsService
    assert 'ACCEPTED'    in ALLOWED_TRANSITIONS['PROVIDER']['PENDING']
    assert 'EN_ROUTE'    in ALLOWED_TRANSITIONS['PROVIDER']['ACCEPTED']
    assert 'IN_PROGRESS' in ALLOWED_TRANSITIONS['PROVIDER']['EN_ROUTE']
    assert 'COMPLETED'   in ALLOWED_TRANSITIONS['PROVIDER']['IN_PROGRESS']
    assert 'CANCELLED'   in ALLOWED_TRANSITIONS['CUSTOMER']['PENDING']
    print('Day 2 - State machine transitions: PASS')

    svc = BookingsService()
    mock_conn = MagicMock()

    # Provider cannot update a booking assigned to someone else
    booking_other = {
        'booking_id': 'b1', 'status': 'PENDING', 'customer_id': 'c1',
        'provider_id': 'other-provider', 'payment_status': 'PENDING',
    }
    with patch.object(svc.modal, 'read_one', return_value=booking_other):
        try:
            svc.update_status(
                {'_user_id': 'my-provider', '_role': 'PROVIDER', 'id': 'b1', 'status': 'ACCEPTED'},
                mock_conn,
            )
            print('Day 2 - Provider ownership check: FAIL')
        except PermissionError:
            print('Day 2 - Provider ownership check: PASS')

    # Provider can update their own booking
    booking_own = {**booking_other, 'provider_id': 'my-provider'}
    with patch.object(svc.modal, 'read_one', return_value=booking_own), \
         patch.object(svc.modal, 'update_status', return_value={**booking_own, 'status': 'ACCEPTED'}), \
         patch.object(svc.notif, 'send_push'):
        status, _ = svc.update_status(
            {'_user_id': 'my-provider', '_role': 'PROVIDER', 'id': 'b1', 'status': 'ACCEPTED'},
            mock_conn,
        )
        assert status == 'success'
        print('Day 2 - Provider valid status update: PASS')

    # Cancel with no payment
    booking_no_pay = {
        'booking_id': 'b2', 'status': 'PENDING', 'customer_id': 'cust1',
        'payment_status': 'PENDING', 'payment_id': None, 'provider_id': None,
    }
    with patch.object(svc.modal, 'read_one', return_value=booking_no_pay), \
         patch.object(svc.modal, 'update_status', return_value={**booking_no_pay, 'status': 'CANCELLED'}), \
         patch.object(svc.notif, 'send_push'):
        status, _ = svc.cancel({'_user_id': 'cust1', '_role': 'CUSTOMER', 'id': 'b2'}, mock_conn)
        assert status == 'success'
        print('Day 2 - Cancel (unpaid): PASS')

    # Cancel with paid booking triggers refund
    booking_paid = {
        'booking_id': 'b3', 'status': 'ACCEPTED', 'customer_id': 'cust1',
        'payment_status': 'PAID', 'payment_id': 'pay-uuid', 'provider_id': None,
    }
    fake_payment = {
        'payment_id': 'pay-uuid', 'razorpay_payment_id': 'rp_pay_123',
        'amount': 50000, 'status': 'PAID',
    }
    with patch.object(svc.modal, 'read_one', return_value=booking_paid), \
         patch.object(svc.modal, 'update_status', return_value={**booking_paid, 'status': 'CANCELLED'}), \
         patch.object(svc.notif, 'send_push'), \
         patch('bookings.bookings_service.razorpay') as mock_rz, \
         patch('bookings.bookings_service.PaymentMaster') as MockPM:
        mock_pm_inst = MockPM.return_value
        mock_pm_inst.find_payment.return_value = fake_payment
        status, _ = svc.cancel({'_user_id': 'cust1', '_role': 'CUSTOMER', 'id': 'b3'}, mock_conn)
        assert status == 'success'
        assert mock_pm_inst.update_payment.called
        print('Day 2 - Cancel (paid, refund triggered): PASS')

    # ─── Day 3: Provider matching ────────────────────────────────────────
    from providers.provider_matching import score_provider, haversine, match_provider

    dist = haversine(17.385, 78.486, 17.395, 78.496)
    assert 1.0 < dist < 2.0
    print(f'Day 3 - Haversine distance: PASS ({dist:.3f} km)')

    p = {'avg_rating': 4.5, 'acceptance_rate': 0.9, 'avg_response_secs': 15,
         'last_lat': 17.390, 'last_lng': 78.490}
    score = score_provider(p, 17.385, 78.486)
    assert 0 < score <= 100
    print(f'Day 3 - Scoring (40+30+20+10): PASS (score={score:.1f})')

    p_no_loc = {'avg_rating': 5.0, 'acceptance_rate': 1.0, 'avg_response_secs': 0}
    assert score_provider(p_no_loc, 17.385, 78.486) == 60.0
    print('Day 3 - No-location fallback (score=60.0): PASS')

    with patch('providers.provider_matching.ProvidersMaster') as MockPM2:
        MockPM2.return_value.get_available_for_service.return_value = []
        assert match_provider(MagicMock(), {'service_id': 'svc1', 'address_snapshot': {}}) is None
        print('Day 3 - match_provider no candidates returns None: PASS')

        MockPM2.return_value.get_available_for_service.return_value = [
            {'provider_id': 'p1', 'avg_rating': 3.0, 'acceptance_rate': 0.5,
             'avg_response_secs': 30, 'last_lat': 17.4, 'last_lng': 78.5},
            {'provider_id': 'p2', 'avg_rating': 5.0, 'acceptance_rate': 1.0,
             'avg_response_secs': 0, 'last_lat': 17.386, 'last_lng': 78.487},
        ]
        best = match_provider(MagicMock(),
                              {'service_id': 'svc1', 'address_snapshot': {'lat': 17.385, 'lng': 78.486}})
        assert best['provider_id'] == 'p2'
        print('Day 3 - match_provider picks highest score: PASS')

    # ─── Day 4: Payments ─────────────────────────────────────────────────
    import hmac as hmac_mod, hashlib
    from payments.payment_service import _verify_signature, PaymentService

    secret = os.environ['RAZORPAY_KEY_SECRET']
    sig = hmac_mod.new(secret.encode(), b'order_A|pay_B', hashlib.sha256).hexdigest()
    assert _verify_signature('order_A', 'pay_B', sig)
    assert not _verify_signature('order_A', 'pay_B', 'wrong')
    print('Day 4 - Razorpay HMAC signature: PASS')

    pay_svc = PaymentService()
    fake_booking_db = {'booking_id': 'b1', 'customer_id': 'cust1', 'total_amount': 59900, 'provider_id': None}

    with patch.object(pay_svc.booking_modal, 'read_one', return_value=fake_booking_db), \
         patch.object(pay_svc.modal, 'create_payment', return_value={'payment_id': 'p1'}), \
         patch('payments.payment_service._get_razorpay_client') as mock_rz:
        mock_rz.return_value.order.create.return_value = {'id': 'order_XYZ'}
        status, data = pay_svc.create_order(
            {'_user_id': 'cust1', '_role': 'CUSTOMER', 'booking_id': 'b1', 'amount': 1},
            MagicMock(),
        )
        assert status == 'success'
        called_amount = mock_rz.return_value.order.create.call_args[0][0]['amount']
        assert called_amount == 59900, f'Expected 59900, got {called_amount}'
        print('Day 4 - create_order uses DB amount (ignores app value): PASS')

    fake_pmt = {'payment_id': 'p2', 'amount': 59900, 'razorpay_payment_id': None}
    fake_bk2 = {'booking_id': 'b1', 'customer_id': 'cust1', 'provider_id': 'prov1'}
    with patch.object(pay_svc.modal, 'find_payment', return_value=fake_pmt), \
         patch.object(pay_svc.modal, 'update_payment'), \
         patch.object(pay_svc.modal, 'add_earning'), \
         patch.object(pay_svc.booking_modal, 'read_one', return_value=fake_bk2), \
         patch.object(pay_svc.booking_modal, 'update_payment'), \
         patch.object(pay_svc.provider_modal, 'update_wallet'), \
         patch.object(pay_svc.notif, 'send_push') as mock_push, \
         patch('payments.payment_service._verify_signature', return_value=True):
        status, data = pay_svc.verify_payment(
            {'_user_id': 'cust1', '_role': 'CUSTOMER',
             'razorpay_order_id': 'ord1', 'razorpay_payment_id': 'pay1',
             'razorpay_signature': 'sig1', 'booking_id': 'b1'},
            MagicMock(),
        )
        assert status == 'success'
        assert mock_push.called
        notify_ids = mock_push.call_args[1]['user_ids']
        assert 'cust1' in notify_ids and 'prov1' in notify_ids
        print('Day 4 - verify_payment: PAID + earnings + push to both parties: PASS')

    # ─── Day 5: Routes registered ─────────────────────────────────────────
    from routing import ROUTES
    booking_routes = [(m, p) for m, p, _, _ in ROUTES if 'booking' in str(p)]
    payment_routes = [(m, p) for m, p, _, _ in ROUTES if 'payment' in str(p)]
    assert len(booking_routes) >= 7, f'Expected >=7, got {len(booking_routes)}'
    assert len(payment_routes) >= 4, f'Expected >=4, got {len(payment_routes)}'
    print(f'Day 5 - Routes: {len(booking_routes)} booking + {len(payment_routes)} payment: PASS')

print()
print('All Week 2 checks: PASSED')
