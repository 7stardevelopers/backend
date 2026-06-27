import sys, os, inspect
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
os.environ['DB_URL'] = 'placeholder'

from unittest.mock import patch
with patch('utilities.db_connection.get_engine'):
    issues = []

    # Day 1: WebSocket
    from web_sockets.web_sockets_service import WebSocketsService, _broadcast_location_to_customer
    src_bcast = inspect.getsource(_broadcast_location_to_customer)
    if 'metadata.tables' in src_bcast:
        issues.append('D1: _broadcast_location_to_customer uses metadata.tables.get() - old pattern')

    src_msg = inspect.getsource(WebSocketsService.on_message)
    if '"CUSTOMER"' in src_msg:
        issues.append('D1: on_message hardcodes _role=CUSTOMER - providers cannot send messages')

    # Day 3: Coupons
    from coupons.coupons_modal import CouponsMaster
    src_modal = inspect.getsource(CouponsMaster)
    if 'from datetime import' in src_modal:
        issues.append('D3: coupons_modal local datetime import inside method (move to top)')
    if 'from sqlalchemy import text' in src_modal:
        issues.append('D3: coupons_modal local sqlalchemy import inside method (move to top)')

    from coupons.coupons_service import CouponsService
    src_validate = inspect.getsource(CouponsService.validate)
    if 'replace(tzinfo' in src_validate:
        issues.append('D3: validate uses .replace(tzinfo=utc) - crashes on tz-aware datetimes from MySQL')

    # Day 3+4: bookings_service.create
    from bookings.bookings_service import BookingsService
    src_create = inspect.getsource(BookingsService.create)
    if 'record_use' not in src_create:
        issues.append('D3: bookings_service.create never calls record_use() - coupon uses never tracked')
    if 'subscription' not in src_create.lower():
        issues.append('D4: bookings_service.create does not apply subscription discount')

    # Day 4: Subscription validator payment_id
    from subscriptions.subscriptions_validator import SubscribeSchema
    try:
        SubscribeSchema(plan_id='p1')
        # no error means payment_id is optional - that's fine
    except Exception:
        issues.append('D4: SubscribeSchema.payment_id is required but should be Optional')

    # Day 5: Routes
    from routing import ROUTES
    route_paths = [str(p) for _, p, _, _ in ROUTES]
    for group in ['/coupons', '/subscriptions', '/reviews', '/notifications']:
        if not any(group in r for r in route_paths):
            issues.append(f'D5: route group missing: {group}')

    from routing_wss import WSS_ROUTES
    for key in ['sendMessage', 'locationUpdate']:
        if key not in WSS_ROUTES:
            issues.append(f'D1: WSS route missing: {key}')
    for key in ['$connect', '$disconnect']:
        if key not in WSS_ROUTES:
            issues.append(f'D1: WSS route missing: {key}')

    print(f'Issues found: {len(issues)}')
    for i, issue in enumerate(issues, 1):
        print(f'  {i}. {issue}')
