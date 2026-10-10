"""Test helpers: an in-memory SQLite database wired into utilities.db_connection
so modals/services run real SQL without MySQL, Redis or AWS."""
import os

os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")

from sqlalchemy import create_engine, text  # noqa: E402

from utilities import db_connection  # noqa: E402

SCHEMA = """
CREATE TABLE users (
    user_id TEXT PRIMARY KEY, phone TEXT, name TEXT, photo_url TEXT, role TEXT,
    status TEXT DEFAULT 'ACTIVE', coins_balance INTEGER DEFAULT 0, referred_by TEXT,
    referral_code TEXT, updated_at TIMESTAMP, created_at TIMESTAMP
);
CREATE TABLE services (
    service_id TEXT PRIMARY KEY, category_id TEXT, name TEXT, base_price INTEGER,
    is_active BOOLEAN DEFAULT 1
);
CREATE TABLE sub_services (
    sub_service_id TEXT PRIMARY KEY, sub_category_id TEXT, service_id TEXT, name TEXT,
    price INTEGER, is_active BOOLEAN DEFAULT 1
);
CREATE TABLE coupons (
    coupon_id TEXT PRIMARY KEY, code TEXT, title TEXT, type TEXT, value INTEGER,
    min_order_amount INTEGER DEFAULT 0, max_discount INTEGER, max_uses INTEGER DEFAULT 1000,
    used_count INTEGER DEFAULT 0, service_ids JSON, expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT 1, created_at TIMESTAMP
);
CREATE TABLE coupon_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, coupon_id TEXT, user_id TEXT, booking_id TEXT,
    created_at TIMESTAMP, UNIQUE (coupon_id, user_id)
);
CREATE TABLE subscription_plans (
    plan_id TEXT PRIMARY KEY, name TEXT, price INTEGER, bookings_included INTEGER,
    discount_pct INTEGER, is_active BOOLEAN DEFAULT 1, sort_order INTEGER DEFAULT 0
);
CREATE TABLE user_subscriptions (
    subscription_id TEXT PRIMARY KEY, user_id TEXT, plan_id TEXT, status TEXT,
    starts_at TIMESTAMP, expires_at TIMESTAMP, bookings_used INTEGER DEFAULT 0,
    payment_id TEXT, created_at TIMESTAMP
);
CREATE TABLE wallet_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, delta INTEGER, reason TEXT,
    booking_id TEXT, created_at TIMESTAMP
);
CREATE TABLE providers (
    provider_id TEXT PRIMARY KEY, user_id TEXT, status TEXT DEFAULT 'APPROVED',
    avg_rating REAL DEFAULT 0, wallet_balance INTEGER DEFAULT 0, is_available BOOLEAN DEFAULT 1,
    bank_account_number TEXT, bank_ifsc TEXT, created_at TIMESTAMP
);
CREATE TABLE provider_services (
    provider_id TEXT, service_id TEXT
);
CREATE TABLE provider_locations (
    provider_id TEXT PRIMARY KEY, lat REAL, lng REAL, updated_at TIMESTAMP
);
CREATE TABLE call_logs (
    call_id TEXT PRIMARY KEY, booking_id TEXT, initiated_by TEXT, target TEXT,
    exotel_call_sid TEXT, status TEXT, duration_sec INTEGER, start_time TIMESTAMP,
    end_time TIMESTAMP, recording_url TEXT, error_message TEXT,
    created_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE TABLE chat_messages (
    message_id TEXT PRIMARY KEY, booking_id TEXT, from_id TEXT, to_id TEXT, text TEXT,
    message_type TEXT DEFAULT 'text', seen_at TIMESTAMP, delivered_at TIMESTAMP,
    created_at TIMESTAMP
);
CREATE TABLE ws_connections (
    connection_id TEXT PRIMARY KEY, user_id TEXT, booking_id TEXT, role TEXT, connected_at TIMESTAMP
);
CREATE TABLE bookings (
    booking_id TEXT PRIMARY KEY, customer_id TEXT, provider_id TEXT, service_id TEXT,
    status TEXT, door_otp TEXT, door_otp_verified BOOLEAN DEFAULT 0,
    otp_attempt_count INTEGER DEFAULT 0, door_otp_generated_at TIMESTAMP,
    scheduled_at TIMESTAMP, service_snapshot JSON, address_snapshot JSON, proof_photos JSON,
    provider_done_at TIMESTAMP, customer_done_at TIMESTAMP, completion_disputed_at TIMESTAMP,
    identity_confirmed_at TIMESTAMP, identity_mismatch_at TIMESTAMP,
    sub_total INTEGER DEFAULT 0, discount INTEGER DEFAULT 0, total_amount INTEGER DEFAULT 0,
    platform_fee INTEGER DEFAULT 0, payment_status TEXT DEFAULT 'PENDING', payment_id TEXT,
<<<<<<< HEAD
    earning_credited_at TIMESTAMP, coupon_id TEXT, requested_provider_id TEXT,
    payment_mode TEXT DEFAULT 'PAY_AFTER', address_id TEXT, is_instant BOOLEAN DEFAULT 0, customer_notes TEXT,
    accepted_at TIMESTAMP, cancellation_fee INTEGER DEFAULT 0, cancel_fee_status TEXT,
    cancel_fee_booking_id TEXT, dues_collected INTEGER DEFAULT 0,
=======
    earning_credited_at TIMESTAMP, coupon_id TEXT, requested_provider_id TEXT, subscription_id TEXT,
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
    updated_at TIMESTAMP, created_at TIMESTAMP
);
CREATE TABLE payments (
    payment_id TEXT PRIMARY KEY, booking_id TEXT, customer_id TEXT, razorpay_order_id TEXT UNIQUE,
    razorpay_payment_id TEXT UNIQUE, amount INTEGER, currency TEXT DEFAULT 'INR',
    status TEXT DEFAULT 'PENDING', payment_method TEXT, refund_id TEXT, refund_amount INTEGER DEFAULT 0,
    purpose TEXT DEFAULT 'BOOKING', plan_id TEXT, paid_at TIMESTAMP, created_at TIMESTAMP
);
CREATE TABLE provider_earnings (
    earning_id TEXT PRIMARY KEY, provider_id TEXT, booking_id TEXT, amount INTEGER, type TEXT,
    created_at TIMESTAMP
);
CREATE TABLE payout_requests (
    payout_id TEXT PRIMARY KEY, provider_id TEXT, amount INTEGER, status TEXT DEFAULT 'PENDING',
    bank_account TEXT, bank_ifsc TEXT, processed_at TIMESTAMP, notes TEXT, created_at TIMESTAMP
);
CREATE TABLE support_tickets (
    ticket_id TEXT PRIMARY KEY, user_id TEXT, subject TEXT, category TEXT, booking_id TEXT,
    priority TEXT, status TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE TABLE identity_reports (
    report_id TEXT PRIMARY KEY, booking_id TEXT UNIQUE, provider_id TEXT, customer_id TEXT,
    ticket_id TEXT, customer_note TEXT, status TEXT DEFAULT 'OPEN', admin_note TEXT,
    resolved_by TEXT, resolved_at TIMESTAMP, created_at TIMESTAMP
);
CREATE TABLE ticket_messages (
    message_id TEXT PRIMARY KEY, ticket_id TEXT, sender_id TEXT, content TEXT,
    is_internal BOOLEAN DEFAULT 0, created_at TIMESTAMP
);
"""


def make_db():
    """Fresh in-memory DB; returns an open connection inside a transaction."""
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                c.execute(text(stmt))
    db_connection.metadata.clear()
    db_connection.metadata.reflect(bind=engine)
    db_connection._engine = engine
    conn = engine.connect()
    conn.begin()
    return conn


def insert(conn, table, **values):
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), values)
