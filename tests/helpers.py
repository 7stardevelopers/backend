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
CREATE TABLE bookings (
    booking_id TEXT PRIMARY KEY, customer_id TEXT, provider_id TEXT, service_id TEXT,
    status TEXT, door_otp TEXT, door_otp_verified BOOLEAN DEFAULT 0,
    otp_attempt_count INTEGER DEFAULT 0, door_otp_generated_at TIMESTAMP,
    updated_at TIMESTAMP, created_at TIMESTAMP
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
