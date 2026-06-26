-- 7StarExperts PostgreSQL Schema
-- Run in order to respect foreign key constraints

-- USERS
CREATE TABLE IF NOT EXISTS users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone         VARCHAR(15) UNIQUE NOT NULL,
    name          VARCHAR(100),
    email         VARCHAR(200),
    photo_url     TEXT,
    role          VARCHAR(20) NOT NULL DEFAULT 'CUSTOMER',
    status        VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    fcm_token     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- USER ADDRESSES
CREATE TABLE IF NOT EXISTS user_addresses (
    address_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    label         VARCHAR(50),
    full_address  TEXT NOT NULL,
    lat           DECIMAL(10,7),
    lng           DECIMAL(10,7),
    pincode       VARCHAR(10),
    city          VARCHAR(100),
    is_default    BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- TOKEN CONNECTIONS (Expo push tokens)
CREATE TABLE IF NOT EXISTS token_connections (
    id            SERIAL PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_id      TEXT NOT NULL UNIQUE,
    device_type   VARCHAR(10),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- REFRESH TOKENS
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    jti           VARCHAR(100) UNIQUE NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked       BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- CATEGORIES
CREATE TABLE IF NOT EXISTS categories (
    category_id   VARCHAR(10) PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    icon          VARCHAR(50),
    color         VARCHAR(7),
    sort_order    INTEGER DEFAULT 0,
    is_active     BOOLEAN DEFAULT TRUE
);

-- SERVICES
CREATE TABLE IF NOT EXISTS services (
    service_id          VARCHAR(10) PRIMARY KEY,
    category_id         VARCHAR(10) NOT NULL REFERENCES categories(category_id),
    name                VARCHAR(100) NOT NULL,
    description         TEXT,
    base_price          INTEGER NOT NULL,
    image_url           TEXT,
    instant_available   BOOLEAN DEFAULT FALSE,
    is_active           BOOLEAN DEFAULT TRUE,
    sort_order          INTEGER DEFAULT 0
);

-- SUB CATEGORIES
CREATE TABLE IF NOT EXISTS sub_categories (
    sub_category_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_id          VARCHAR(10) NOT NULL REFERENCES services(service_id),
    name                VARCHAR(100) NOT NULL,
    sort_order          INTEGER DEFAULT 0
);

-- SUB SERVICES
CREATE TABLE IF NOT EXISTS sub_services (
    sub_service_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sub_category_id     UUID NOT NULL REFERENCES sub_categories(sub_category_id),
    service_id          VARCHAR(10) NOT NULL REFERENCES services(service_id),
    name                VARCHAR(200) NOT NULL,
    description         TEXT,
    price               INTEGER NOT NULL,
    duration_minutes    INTEGER,
    is_active           BOOLEAN DEFAULT TRUE
);

-- PROVIDERS
CREATE TABLE IF NOT EXISTS providers (
    provider_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID UNIQUE NOT NULL REFERENCES users(user_id),
    status              VARCHAR(20) DEFAULT 'PENDING',
    is_available        BOOLEAN DEFAULT FALSE,
    last_lat            DECIMAL(10,7),
    last_lng            DECIMAL(10,7),
    last_seen_at        TIMESTAMPTZ,
    avg_rating          DECIMAL(3,2) DEFAULT 0,
    total_reviews       INTEGER DEFAULT 0,
    acceptance_rate     DECIMAL(5,4) DEFAULT 1.0,
    avg_response_secs   INTEGER DEFAULT 30,
    wallet_balance      INTEGER DEFAULT 0,
    bank_account_number VARCHAR(30),
    bank_ifsc           VARCHAR(15),
    bio                 TEXT,
    years_experience    INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- PROVIDER SERVICES
CREATE TABLE IF NOT EXISTS provider_services (
    id                  SERIAL PRIMARY KEY,
    provider_id         UUID NOT NULL REFERENCES providers(provider_id),
    service_id          VARCHAR(10) NOT NULL REFERENCES services(service_id),
    custom_price        INTEGER,
    UNIQUE(provider_id, service_id)
);

-- PROVIDER DOCUMENTS
CREATE TABLE IF NOT EXISTS provider_documents (
    document_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id         UUID NOT NULL REFERENCES providers(provider_id),
    doc_type            VARCHAR(30) NOT NULL,
    file_url            TEXT NOT NULL,
    status              VARCHAR(20) DEFAULT 'PENDING',
    rejection_reason    TEXT,
    verified_at         TIMESTAMPTZ,
    verified_by         UUID REFERENCES users(user_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- COUPONS
CREATE TABLE IF NOT EXISTS coupons (
    coupon_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                VARCHAR(30) UNIQUE NOT NULL,
    title               VARCHAR(100) NOT NULL,
    type                VARCHAR(10) NOT NULL,
    value               INTEGER NOT NULL,
    min_order_amount    INTEGER DEFAULT 0,
    max_discount        INTEGER,
    max_uses            INTEGER DEFAULT 1000,
    used_count          INTEGER DEFAULT 0,
    service_ids         TEXT[],
    expires_at          TIMESTAMPTZ NOT NULL,
    source              VARCHAR(20) DEFAULT 'MANUAL',
    color               VARCHAR(7),
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- BOOKINGS
CREATE TABLE IF NOT EXISTS bookings (
    booking_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         UUID NOT NULL REFERENCES users(user_id),
    provider_id         UUID REFERENCES providers(provider_id),
    service_id          VARCHAR(10) NOT NULL REFERENCES services(service_id),
    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    scheduled_at        TIMESTAMPTZ NOT NULL,
    address_id          UUID REFERENCES user_addresses(address_id),
    address_snapshot    JSONB,
    service_snapshot    JSONB,
    sub_total           INTEGER NOT NULL,
    discount            INTEGER DEFAULT 0,
    total_amount        INTEGER NOT NULL,
    platform_fee        INTEGER DEFAULT 0,
    coupon_id           UUID REFERENCES coupons(coupon_id),
    payment_status      VARCHAR(20) DEFAULT 'PENDING',
    payment_id          UUID,
    is_instant          BOOLEAN DEFAULT FALSE,
    door_otp            VARCHAR(4),
    door_otp_verified   BOOLEAN DEFAULT FALSE,
    proof_photos        TEXT[],
    customer_notes      TEXT,
    cancellation_reason TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- BOOKING ITEMS
CREATE TABLE IF NOT EXISTS booking_items (
    item_id             SERIAL PRIMARY KEY,
    booking_id          UUID NOT NULL REFERENCES bookings(booking_id),
    sub_service_id      UUID NOT NULL REFERENCES sub_services(sub_service_id),
    name_snapshot       VARCHAR(200),
    price_snapshot      INTEGER,
    quantity            INTEGER DEFAULT 1
);

-- INSTANT BOOKINGS
CREATE TABLE IF NOT EXISTS instant_bookings (
    instant_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id          UUID NOT NULL REFERENCES bookings(booking_id),
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at       TIMESTAMPTZ,
    provider_assigned_at TIMESTAMPTZ,
    sla_minutes         INTEGER DEFAULT 60,
    surge_applied       BOOLEAN DEFAULT FALSE,
    surge_pct           INTEGER DEFAULT 0,
    status              VARCHAR(20) DEFAULT 'DISPATCHING'
);

-- PAYMENTS
CREATE TABLE IF NOT EXISTS payments (
    payment_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id          UUID NOT NULL REFERENCES bookings(booking_id),
    customer_id         UUID NOT NULL REFERENCES users(user_id),
    razorpay_order_id   VARCHAR(100) UNIQUE,
    razorpay_payment_id VARCHAR(100) UNIQUE,
    amount              INTEGER NOT NULL,
    currency            VARCHAR(5) DEFAULT 'INR',
    status              VARCHAR(20) DEFAULT 'PENDING',
    payment_method      VARCHAR(30),
    refund_id           VARCHAR(100),
    refund_amount       INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- TIPS
CREATE TABLE IF NOT EXISTS tips (
    tip_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id          UUID NOT NULL REFERENCES bookings(booking_id),
    customer_id         UUID NOT NULL REFERENCES users(user_id),
    provider_id         UUID NOT NULL REFERENCES providers(provider_id),
    amount              INTEGER NOT NULL,
    payment_id          UUID REFERENCES payments(payment_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- REVIEWS
CREATE TABLE IF NOT EXISTS reviews (
    review_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id          UUID UNIQUE NOT NULL REFERENCES bookings(booking_id),
    customer_id         UUID NOT NULL REFERENCES users(user_id),
    provider_id         UUID NOT NULL REFERENCES providers(provider_id),
    service_id          VARCHAR(10) NOT NULL,
    rating              SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment             TEXT,
    is_deleted          BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- COUPON USES
CREATE TABLE IF NOT EXISTS coupon_uses (
    id                  SERIAL PRIMARY KEY,
    coupon_id           UUID NOT NULL REFERENCES coupons(coupon_id),
    user_id             UUID NOT NULL REFERENCES users(user_id),
    booking_id          UUID NOT NULL REFERENCES bookings(booking_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(coupon_id, user_id)
);

-- SUBSCRIPTION PLANS
CREATE TABLE IF NOT EXISTS subscription_plans (
    plan_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(100) NOT NULL,
    price               INTEGER NOT NULL,
    bookings_included   INTEGER,
    discount_pct        INTEGER NOT NULL,
    features            JSONB,
    is_active           BOOLEAN DEFAULT TRUE,
    sort_order          INTEGER DEFAULT 0
);

-- USER SUBSCRIPTIONS
CREATE TABLE IF NOT EXISTS user_subscriptions (
    subscription_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(user_id),
    plan_id             UUID NOT NULL REFERENCES subscription_plans(plan_id),
    status              VARCHAR(20) DEFAULT 'ACTIVE',
    starts_at           TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    bookings_used       INTEGER DEFAULT 0,
    payment_id          UUID REFERENCES payments(payment_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- PROVIDER EARNINGS
CREATE TABLE IF NOT EXISTS provider_earnings (
    earning_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id         UUID NOT NULL REFERENCES providers(provider_id),
    booking_id          UUID REFERENCES bookings(booking_id),
    amount              INTEGER NOT NULL,
    type                VARCHAR(20),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- PAYOUT REQUESTS
CREATE TABLE IF NOT EXISTS payout_requests (
    payout_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id         UUID NOT NULL REFERENCES providers(provider_id),
    amount              INTEGER NOT NULL,
    status              VARCHAR(20) DEFAULT 'PENDING',
    bank_account        VARCHAR(30),
    bank_ifsc           VARCHAR(15),
    processed_at        TIMESTAMPTZ,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SUPPORT TICKETS
CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(user_id),
    booking_id          UUID REFERENCES bookings(booking_id),
    category            VARCHAR(50),
    subject             VARCHAR(200) NOT NULL,
    status              VARCHAR(20) DEFAULT 'OPEN',
    priority            VARCHAR(10) DEFAULT 'MEDIUM',
    assigned_to         UUID REFERENCES users(user_id),
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- TICKET MESSAGES
CREATE TABLE IF NOT EXISTS ticket_messages (
    message_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id           UUID NOT NULL REFERENCES support_tickets(ticket_id),
    sender_id           UUID NOT NULL REFERENCES users(user_id),
    content             TEXT NOT NULL,
    is_internal         BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- IN-APP NOTIFICATIONS
CREATE TABLE IF NOT EXISTS in_app_notifications (
    notification_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(user_id),
    title               VARCHAR(200) NOT NULL,
    body                TEXT,
    type                VARCHAR(50),
    data                JSONB,
    read_ind            BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ACTIVITY LOG
CREATE TABLE IF NOT EXISTS activity_log (
    log_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID REFERENCES users(user_id),
    action              VARCHAR(100) NOT NULL,
    entity_type         VARCHAR(50),
    entity_id           UUID,
    metadata            JSONB,
    ip_address          INET,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- CHAT MESSAGES
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id      UUID NOT NULL REFERENCES bookings(booking_id),
    from_id         UUID NOT NULL REFERENCES users(user_id),
    to_id           UUID NOT NULL REFERENCES users(user_id),
    text            TEXT NOT NULL,
    message_type    VARCHAR(20) DEFAULT 'text',
    seen_at         TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- WEBSOCKET CONNECTIONS
CREATE TABLE IF NOT EXISTS ws_connections (
    connection_id   VARCHAR(200) PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(user_id),
    booking_id      UUID REFERENCES bookings(booking_id),
    connected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- PROVIDER LIVE LOCATIONS
CREATE TABLE IF NOT EXISTS provider_locations (
    provider_id     UUID PRIMARY KEY REFERENCES providers(provider_id),
    lat             DECIMAL(10,7) NOT NULL,
    lng             DECIMAL(10,7) NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_bookings_customer_id ON bookings(customer_id);
CREATE INDEX IF NOT EXISTS idx_bookings_provider_id ON bookings(provider_id);
CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status);
CREATE INDEX IF NOT EXISTS idx_bookings_scheduled_at ON bookings(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_providers_status_available ON providers(status, is_available);
CREATE INDEX IF NOT EXISTS idx_reviews_provider_id ON reviews(provider_id);
CREATE INDEX IF NOT EXISTS idx_in_app_notifications_user ON in_app_notifications(user_id, read_ind);
CREATE INDEX IF NOT EXISTS idx_token_connections_user ON token_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_coupon_uses_user_coupon ON coupon_uses(user_id, coupon_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_booking ON chat_messages(booking_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ws_connections_user ON ws_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_user ON activity_log(user_id, created_at DESC);

-- SEED DATA: Categories
INSERT INTO categories (category_id, name, icon, color, sort_order) VALUES
  ('c1', 'Cleaning', 'sparkles', '#3B82F6', 1),
  ('c2', 'Beauty', 'face-woman', '#EC4899', 2),
  ('c3', 'Appliance Repair', 'wrench', '#F59E0B', 3),
  ('c4', 'Electrical', 'lightning-bolt', '#EAB308', 4),
  ('c5', 'Plumbing', 'water', '#06B6D4', 5),
  ('c6', 'Painting', 'paint-bucket', '#8B5CF6', 6),
  ('c7', 'Carpentry', 'hammer', '#78350F', 7),
  ('c8', 'Pest Control', 'bug', '#84CC16', 8),
  ('c9', 'AC Service', 'snowflake', '#0EA5E9', 9)
ON CONFLICT (category_id) DO NOTHING;

-- SEED DATA: Services
INSERT INTO services (service_id, category_id, name, description, base_price, instant_available, sort_order) VALUES
  ('s1', 'c1', 'Deep Home Cleaning', 'Complete deep cleaning of your entire home', 199900, false, 1),
  ('s2', 'c1', 'Bathroom Cleaning', 'Professional bathroom scrub and sanitization', 99900, false, 2),
  ('s3', 'c5', 'Plumbing', 'Leak fix, pipe repair, tap installation', 49900, true, 1),
  ('s4', 'c4', 'Electrical', 'Switch, socket, fan, light installation & repair', 49900, true, 1),
  ('s5', 'c9', 'AC Service & Repair', 'AC gas refill, cleaning, installation', 149900, false, 1),
  ('s6', 'c3', 'Appliance Repair', 'Washing machine, refrigerator, microwave repair', 79900, false, 1),
  ('s7', 'c7', 'Carpentry', 'Furniture assembly, door fix, custom woodwork', 59900, true, 1),
  ('s8', 'c6', 'Home Painting', 'Interior and exterior painting', 299900, false, 1),
  ('s9', 'c8', 'Pest Control', 'Cockroach, ant, termite, bed bug treatment', 129900, false, 1)
ON CONFLICT (service_id) DO NOTHING;

-- SEED DATA: Subscription Plans
INSERT INTO subscription_plans (name, price, bookings_included, discount_pct, sort_order, features) VALUES
  ('Basic Pass', 29900, 3, 10, 1, '{"highlights": ["3 bookings/month", "10% off each booking", "Priority support"]}'),
  ('Star Pass', 59900, 8, 20, 2, '{"highlights": ["8 bookings/month", "20% off each booking", "Free re-service if not satisfied"]}'),
  ('Pro Pass', 99900, NULL, 30, 3, '{"highlights": ["Unlimited bookings", "30% off each booking", "Priority matching", "Dedicated account manager"]}')
ON CONFLICT DO NOTHING;
