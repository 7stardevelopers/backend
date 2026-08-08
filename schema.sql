-- 7StarExperts MySQL Schema
-- Run in order to respect foreign key constraints.
-- All tables InnoDB / utf8mb4 (set the DB defaults below before running).

-- Run once before creating tables (skip if the DB already exists):
-- CREATE DATABASE sevenstar CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- USE sevenstar;

SET default_storage_engine = InnoDB;

-- USERS
CREATE TABLE IF NOT EXISTS users (
    user_id       CHAR(36) NOT NULL DEFAULT (UUID()),
    phone         VARCHAR(15) NOT NULL,
    name          VARCHAR(100),
    email         VARCHAR(200),
    photo_url     TEXT,
    role          VARCHAR(20) NOT NULL DEFAULT 'CUSTOMER',
    status        VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    fcm_token     TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_phone (phone)
);

-- USER ADDRESSES
CREATE TABLE IF NOT EXISTS user_addresses (
    address_id    CHAR(36) NOT NULL DEFAULT (UUID()),
    user_id       CHAR(36) NOT NULL,
    label         VARCHAR(50),
    full_address  TEXT NOT NULL,
    lat           DECIMAL(10,7),
    lng           DECIMAL(10,7),
    pincode       VARCHAR(10),
    city          VARCHAR(100),
    is_default    BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (address_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- TOKEN CONNECTIONS (Expo push tokens)
CREATE TABLE IF NOT EXISTS token_connections (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id       CHAR(36) NOT NULL,
    token_id      VARCHAR(255) NOT NULL,
    device_type   VARCHAR(10),
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_token_connections_token (token_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- REFRESH TOKENS
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_id      CHAR(36) NOT NULL DEFAULT (UUID()),
    user_id       CHAR(36) NOT NULL,
    jti           VARCHAR(100) NOT NULL,
    expires_at    TIMESTAMP NOT NULL,
    revoked       BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (token_id),
    UNIQUE KEY uq_refresh_tokens_jti (jti),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
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
    category_id         VARCHAR(10) NOT NULL,
    name                VARCHAR(100) NOT NULL,
    description         TEXT,
    base_price          INTEGER NOT NULL,
    image_url           TEXT,
    instant_available   BOOLEAN DEFAULT FALSE,
    is_active           BOOLEAN DEFAULT TRUE,
    sort_order          INTEGER DEFAULT 0,
    FOREIGN KEY (category_id) REFERENCES categories(category_id)
);

-- SUB CATEGORIES
CREATE TABLE IF NOT EXISTS sub_categories (
    sub_category_id     CHAR(36) NOT NULL DEFAULT (UUID()),
    service_id          VARCHAR(10) NOT NULL,
    name                VARCHAR(100) NOT NULL,
    sort_order          INTEGER DEFAULT 0,
    PRIMARY KEY (sub_category_id),
    FOREIGN KEY (service_id) REFERENCES services(service_id)
);

-- SUB SERVICES
CREATE TABLE IF NOT EXISTS sub_services (
    sub_service_id      CHAR(36) NOT NULL DEFAULT (UUID()),
    sub_category_id     CHAR(36) NOT NULL,
    service_id          VARCHAR(10) NOT NULL,
    name                VARCHAR(200) NOT NULL,
    description         TEXT,
    price               INTEGER NOT NULL,
    duration_minutes    INTEGER,
    is_active           BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (sub_service_id),
    FOREIGN KEY (sub_category_id) REFERENCES sub_categories(sub_category_id),
    FOREIGN KEY (service_id) REFERENCES services(service_id)
);

-- PROVIDERS
CREATE TABLE IF NOT EXISTS providers (
    provider_id         CHAR(36) NOT NULL DEFAULT (UUID()),
    user_id             CHAR(36) NOT NULL,
    status              VARCHAR(20) DEFAULT 'PENDING',
    is_available        BOOLEAN DEFAULT FALSE,
    last_lat            DECIMAL(10,7),
    last_lng            DECIMAL(10,7),
    last_seen_at        TIMESTAMP NULL,
    avg_rating          DECIMAL(3,2) DEFAULT 0,
    total_reviews       INTEGER DEFAULT 0,
    acceptance_rate     DECIMAL(5,4) DEFAULT 1.0,
    avg_response_secs   INTEGER DEFAULT 30,
    wallet_balance      INTEGER DEFAULT 0,
    bank_account_number VARCHAR(30),
    bank_ifsc           VARCHAR(15),
    bank_account_name   VARCHAR(100),
    bio                 TEXT,
    years_experience    INTEGER DEFAULT 0,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_id),
    UNIQUE KEY uq_providers_user_id (user_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- PROVIDER SERVICES
CREATE TABLE IF NOT EXISTS provider_services (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    provider_id         CHAR(36) NOT NULL,
    service_id          VARCHAR(10) NOT NULL,
    custom_price        INTEGER,
    UNIQUE KEY uq_provider_services (provider_id, service_id),
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id),
    FOREIGN KEY (service_id) REFERENCES services(service_id)
);

-- PROVIDER DOCUMENTS
CREATE TABLE IF NOT EXISTS provider_documents (
    document_id         CHAR(36) NOT NULL DEFAULT (UUID()),
    provider_id         CHAR(36) NOT NULL,
    doc_type            VARCHAR(30) NOT NULL,
    file_url            TEXT NOT NULL,
    status              VARCHAR(20) DEFAULT 'PENDING',
    rejection_reason    TEXT,
    verified_at         TIMESTAMP NULL,
    verified_by         CHAR(36),
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (document_id),
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id),
    FOREIGN KEY (verified_by) REFERENCES users(user_id)
);

-- COUPONS
CREATE TABLE IF NOT EXISTS coupons (
    coupon_id           CHAR(36) NOT NULL DEFAULT (UUID()),
    code                VARCHAR(30) NOT NULL,
    title               VARCHAR(100) NOT NULL,
    type                VARCHAR(10) NOT NULL,
    value               INTEGER NOT NULL,
    min_order_amount    INTEGER DEFAULT 0,
    max_discount        INTEGER,
    max_uses            INTEGER DEFAULT 1000,
    used_count          INTEGER DEFAULT 0,
    service_ids         JSON,
    expires_at          TIMESTAMP NOT NULL,
    source              VARCHAR(20) DEFAULT 'MANUAL',
    color               VARCHAR(7),
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (coupon_id),
    UNIQUE KEY uq_coupons_code (code)
);

-- BOOKINGS
CREATE TABLE IF NOT EXISTS bookings (
    booking_id          CHAR(36) NOT NULL DEFAULT (UUID()),
    customer_id         CHAR(36) NOT NULL,
    provider_id         CHAR(36),
    service_id          VARCHAR(10) NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    scheduled_at        TIMESTAMP NOT NULL,
    address_id          CHAR(36),
    address_snapshot    JSON,
    service_snapshot    JSON,
    sub_total           INTEGER NOT NULL,
    discount            INTEGER DEFAULT 0,
    total_amount        INTEGER NOT NULL,
    platform_fee        INTEGER DEFAULT 0,
    coupon_id           CHAR(36),
    payment_status      VARCHAR(20) DEFAULT 'PENDING',
    payment_id          CHAR(36),
    is_instant          BOOLEAN DEFAULT FALSE,
    door_otp            VARCHAR(4),
    door_otp_verified   BOOLEAN DEFAULT FALSE,
    proof_photos        JSON,
    customer_notes      TEXT,
    cancellation_reason TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (booking_id),
    FOREIGN KEY (customer_id) REFERENCES users(user_id),
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id),
    FOREIGN KEY (service_id) REFERENCES services(service_id),
    FOREIGN KEY (address_id) REFERENCES user_addresses(address_id),
    FOREIGN KEY (coupon_id) REFERENCES coupons(coupon_id)
);

-- BOOKING ITEMS
CREATE TABLE IF NOT EXISTS booking_items (
    item_id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    booking_id          CHAR(36) NOT NULL,
    sub_service_id      CHAR(36) NOT NULL,
    name_snapshot       VARCHAR(200),
    price_snapshot      INTEGER,
    quantity            INTEGER DEFAULT 1,
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id),
    FOREIGN KEY (sub_service_id) REFERENCES sub_services(sub_service_id)
);

-- INSTANT BOOKINGS
CREATE TABLE IF NOT EXISTS instant_bookings (
    instant_id           CHAR(36) NOT NULL DEFAULT (UUID()),
    booking_id           CHAR(36) NOT NULL,
    requested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dispatched_at        TIMESTAMP NULL,
    provider_assigned_at TIMESTAMP NULL,
    sla_minutes          INTEGER DEFAULT 60,
    surge_applied        BOOLEAN DEFAULT FALSE,
    surge_pct            INTEGER DEFAULT 0,
    status               VARCHAR(20) DEFAULT 'DISPATCHING',
    PRIMARY KEY (instant_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
);

-- PAYMENTS
CREATE TABLE IF NOT EXISTS payments (
    payment_id          CHAR(36) NOT NULL DEFAULT (UUID()),
    booking_id          CHAR(36) NOT NULL,
    customer_id         CHAR(36) NOT NULL,
    razorpay_order_id   VARCHAR(100),
    razorpay_payment_id VARCHAR(100),
    amount              INTEGER NOT NULL,
    currency            VARCHAR(5) DEFAULT 'INR',
    status              VARCHAR(20) DEFAULT 'PENDING',
    payment_method      VARCHAR(30),
    refund_id           VARCHAR(100),
    refund_amount       INTEGER DEFAULT 0,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (payment_id),
    UNIQUE KEY uq_payments_rzp_order (razorpay_order_id),
    UNIQUE KEY uq_payments_rzp_payment (razorpay_payment_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id),
    FOREIGN KEY (customer_id) REFERENCES users(user_id)
);

-- TIPS
CREATE TABLE IF NOT EXISTS tips (
    tip_id              CHAR(36) NOT NULL DEFAULT (UUID()),
    booking_id          CHAR(36) NOT NULL,
    customer_id         CHAR(36) NOT NULL,
    provider_id         CHAR(36) NOT NULL,
    amount              INTEGER NOT NULL,
    payment_id          CHAR(36),
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tip_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id),
    FOREIGN KEY (customer_id) REFERENCES users(user_id),
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id),
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id)
);

-- REVIEWS
CREATE TABLE IF NOT EXISTS reviews (
    review_id           CHAR(36) NOT NULL DEFAULT (UUID()),
    booking_id          CHAR(36) NOT NULL,
    customer_id         CHAR(36) NOT NULL,
    provider_id         CHAR(36) NOT NULL,
    service_id          VARCHAR(10) NOT NULL,
    rating              SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment             TEXT,
    is_deleted          BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (review_id),
    UNIQUE KEY uq_reviews_booking (booking_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id),
    FOREIGN KEY (customer_id) REFERENCES users(user_id),
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id)
);

-- PROVIDER REVIEWS (worker reviews customer)
CREATE TABLE IF NOT EXISTS provider_reviews (
    review_id    CHAR(36)   NOT NULL DEFAULT (UUID()),
    booking_id   CHAR(36)   NOT NULL,
    provider_id  CHAR(36)   NOT NULL,
    customer_id  CHAR(36)   NOT NULL,
    rating       SMALLINT   NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment      TEXT,
    is_deleted   BOOLEAN    DEFAULT FALSE,
    created_at   TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (review_id),
    UNIQUE KEY uq_provider_reviews_booking (booking_id),
    FOREIGN KEY (booking_id)  REFERENCES bookings(booking_id),
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id),
    FOREIGN KEY (customer_id) REFERENCES users(user_id)
);

-- COUPON USES
CREATE TABLE IF NOT EXISTS coupon_uses (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    coupon_id           CHAR(36) NOT NULL,
    user_id             CHAR(36) NOT NULL,
    booking_id          CHAR(36) NOT NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_coupon_uses (coupon_id, user_id),
    FOREIGN KEY (coupon_id) REFERENCES coupons(coupon_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
);

-- SUBSCRIPTION PLANS
CREATE TABLE IF NOT EXISTS subscription_plans (
    plan_id             CHAR(36) NOT NULL DEFAULT (UUID()),
    name                VARCHAR(100) NOT NULL,
    price               INTEGER NOT NULL,
    bookings_included   INTEGER,
    discount_pct        INTEGER NOT NULL,
    features            JSON,
    is_active           BOOLEAN DEFAULT TRUE,
    sort_order          INTEGER DEFAULT 0,
    PRIMARY KEY (plan_id),
    UNIQUE KEY uq_subscription_plans_name (name)
);

-- USER SUBSCRIPTIONS
CREATE TABLE IF NOT EXISTS user_subscriptions (
    subscription_id     CHAR(36) NOT NULL DEFAULT (UUID()),
    user_id             CHAR(36) NOT NULL,
    plan_id             CHAR(36) NOT NULL,
    status              VARCHAR(20) DEFAULT 'ACTIVE',
    starts_at           TIMESTAMP NOT NULL,
    expires_at          TIMESTAMP NOT NULL,
    bookings_used       INTEGER DEFAULT 0,
    payment_id          CHAR(36),
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (subscription_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (plan_id) REFERENCES subscription_plans(plan_id),
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id)
);

-- PROVIDER EARNINGS
CREATE TABLE IF NOT EXISTS provider_earnings (
    earning_id          CHAR(36) NOT NULL DEFAULT (UUID()),
    provider_id         CHAR(36) NOT NULL,
    booking_id          CHAR(36),
    amount              INTEGER NOT NULL,
    type                VARCHAR(20),
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (earning_id),
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
);

-- PAYOUT REQUESTS
CREATE TABLE IF NOT EXISTS payout_requests (
    payout_id           CHAR(36) NOT NULL DEFAULT (UUID()),
    provider_id         CHAR(36) NOT NULL,
    amount              INTEGER NOT NULL,
    status              VARCHAR(20) DEFAULT 'PENDING',
    bank_account        VARCHAR(30),
    bank_ifsc           VARCHAR(15),
    processed_at        TIMESTAMP NULL,
    notes               TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (payout_id),
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id)
);

-- SUPPORT TICKETS
CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id           CHAR(36) NOT NULL DEFAULT (UUID()),
    user_id             CHAR(36) NOT NULL,
    booking_id          CHAR(36),
    category            VARCHAR(50),
    subject             VARCHAR(200) NOT NULL,
    status              VARCHAR(20) DEFAULT 'OPEN',
    priority            VARCHAR(10) DEFAULT 'MEDIUM',
    assigned_to         CHAR(36),
    resolved_at         TIMESTAMP NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ticket_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id),
    FOREIGN KEY (assigned_to) REFERENCES users(user_id)
);

-- TICKET MESSAGES
CREATE TABLE IF NOT EXISTS ticket_messages (
    message_id          CHAR(36) NOT NULL DEFAULT (UUID()),
    ticket_id           CHAR(36) NOT NULL,
    sender_id           CHAR(36) NOT NULL,
    content             TEXT NOT NULL,
    is_internal         BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id),
    FOREIGN KEY (ticket_id) REFERENCES support_tickets(ticket_id),
    FOREIGN KEY (sender_id) REFERENCES users(user_id)
);

-- IN-APP NOTIFICATIONS
CREATE TABLE IF NOT EXISTS in_app_notifications (
    notification_id     CHAR(36) NOT NULL DEFAULT (UUID()),
    user_id             CHAR(36) NOT NULL,
    title               VARCHAR(200) NOT NULL,
    body                TEXT,
    type                VARCHAR(50),
    data                JSON,
    read_ind            BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (notification_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- ACTIVITY LOG
CREATE TABLE IF NOT EXISTS activity_log (
    log_id              CHAR(36) NOT NULL DEFAULT (UUID()),
    user_id             CHAR(36),
    action              VARCHAR(100) NOT NULL,
    entity_type         VARCHAR(50),
    entity_id           CHAR(36),
    metadata            JSON,
    ip_address          VARCHAR(45),
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (log_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- CHAT MESSAGES
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id      CHAR(36) NOT NULL DEFAULT (UUID()),
    booking_id      CHAR(36) NOT NULL,
    from_id         CHAR(36) NOT NULL,
    to_id           CHAR(36) NOT NULL,
    text            TEXT NOT NULL,
    message_type    VARCHAR(20) DEFAULT 'text',
    seen_at         TIMESTAMP NULL,
    delivered_at    TIMESTAMP NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id),
    FOREIGN KEY (from_id) REFERENCES users(user_id),
    FOREIGN KEY (to_id) REFERENCES users(user_id)
);

-- WEBSOCKET CONNECTIONS
CREATE TABLE IF NOT EXISTS ws_connections (
    connection_id   VARCHAR(200) PRIMARY KEY,
    user_id         CHAR(36) NOT NULL,
    booking_id      CHAR(36),
    role            VARCHAR(20),
    connected_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
);

-- ANNOUNCEMENTS
CREATE TABLE IF NOT EXISTS announcements (
    announcement_id CHAR(36)     NOT NULL DEFAULT (UUID()),
    title           VARCHAR(255) NOT NULL,
    body            TEXT         NOT NULL,
    target_role     VARCHAR(20)  NOT NULL DEFAULT 'ALL',
    sent_by         CHAR(36),
    sent_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (announcement_id),
    FOREIGN KEY (sent_by) REFERENCES users(user_id)
);

-- PROVIDER LIVE LOCATIONS
CREATE TABLE IF NOT EXISTS provider_locations (
    provider_id     CHAR(36) PRIMARY KEY,
    lat             DECIMAL(10,7) NOT NULL,
    lng             DECIMAL(10,7) NOT NULL,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id)
);

-- INDEXES
-- MySQL has no CREATE INDEX IF NOT EXISTS.
-- This guard procedure skips any index that already exists, so the block is safe to re-run.
DROP PROCEDURE IF EXISTS _add_index;
DELIMITER //
CREATE PROCEDURE _add_index(tbl VARCHAR(64), idx VARCHAR(64), col_expr VARCHAR(255))
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = tbl AND index_name = idx
    ) THEN
        SET @s = CONCAT('CREATE INDEX ', idx, ' ON ', tbl, '(', col_expr, ')');
        PREPARE stmt FROM @s;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END //
DELIMITER ;

CALL _add_index('bookings',              'idx_bookings_customer_id',        'customer_id');
CALL _add_index('bookings',              'idx_bookings_provider_id',        'provider_id');
CALL _add_index('bookings',              'idx_bookings_status',             'status');
CALL _add_index('bookings',              'idx_bookings_scheduled_at',       'scheduled_at');
CALL _add_index('providers',             'idx_providers_status_available',  'status, is_available');
CALL _add_index('reviews',               'idx_reviews_provider_id',        'provider_id');
CALL _add_index('in_app_notifications',  'idx_in_app_notifications_user',  'user_id, read_ind');
CALL _add_index('token_connections',     'idx_token_connections_user',     'user_id');
CALL _add_index('chat_messages',         'idx_chat_messages_booking',      'booking_id, created_at DESC');
CALL _add_index('ws_connections',        'idx_ws_connections_user',        'user_id');
CALL _add_index('activity_log',          'idx_activity_log_user',          'user_id, created_at DESC');

DROP PROCEDURE IF EXISTS _add_index;

-- SEED DATA: Categories
INSERT IGNORE INTO categories (category_id, name, icon, color, sort_order) VALUES
  ('c1', 'Cleaning', 'sparkles', '#3B82F6', 1),
  ('c2', 'Beauty', 'face-woman', '#EC4899', 2),
  ('c3', 'Appliance Repair', 'wrench', '#F59E0B', 3),
  ('c4', 'Electrical', 'lightning-bolt', '#EAB308', 4),
  ('c5', 'Plumbing', 'water', '#06B6D4', 5),
  ('c6', 'Painting', 'paint-bucket', '#8B5CF6', 6),
  ('c7', 'Carpentry', 'hammer', '#78350F', 7),
  ('c8', 'Pest Control', 'bug', '#84CC16', 8),
  ('c9', 'AC Service', 'snowflake', '#0EA5E9', 9);

-- SEED DATA: Services
INSERT IGNORE INTO services (service_id, category_id, name, description, base_price, instant_available, sort_order) VALUES
  ('s1', 'c1', 'Deep Home Cleaning', 'Complete deep cleaning of your entire home', 199900, FALSE, 1),
  ('s2', 'c1', 'Bathroom Cleaning', 'Professional bathroom scrub and sanitization', 99900, FALSE, 2),
  ('s3', 'c5', 'Plumbing', 'Leak fix, pipe repair, tap installation', 49900, TRUE, 1),
  ('s4', 'c4', 'Electrical', 'Switch, socket, fan, light installation & repair', 49900, TRUE, 1),
  ('s5', 'c9', 'AC Service & Repair', 'AC gas refill, cleaning, installation', 149900, FALSE, 1),
  ('s6', 'c3', 'Appliance Repair', 'Washing machine, refrigerator, microwave repair', 79900, FALSE, 1),
  ('s7', 'c7', 'Carpentry', 'Furniture assembly, door fix, custom woodwork', 59900, TRUE, 1),
  ('s8', 'c6', 'Home Painting', 'Interior and exterior painting', 299900, FALSE, 1),
  ('s9', 'c8', 'Pest Control', 'Cockroach, ant, termite, bed bug treatment', 129900, FALSE, 1);

-- SEED DATA: Subscription Plans
INSERT IGNORE INTO subscription_plans (name, price, bookings_included, discount_pct, sort_order, features) VALUES
  ('Basic Pass', 29900, 3, 10, 1, '{"highlights": ["3 bookings/month", "10% off each booking", "Priority support"]}'),
  ('Star Pass', 59900, 8, 20, 2, '{"highlights": ["8 bookings/month", "20% off each booking", "Free re-service if not satisfied"]}'),
  ('Pro Pass', 99900, NULL, 30, 3, '{"highlights": ["Unlimited bookings", "30% off each booking", "Priority matching", "Dedicated account manager"]}');
