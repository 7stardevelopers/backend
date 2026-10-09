-- 005: Razorpay hardening — idempotent payments, plan purchases, earnings on completion.
-- Run against staging, then production (safe to re-run):
--   mysql -h <host> -u <user> -p <db> < migrations/005_payment_hardening.sql
--
-- Run this BEFORE deploying the backend that uses these columns.
--   payments.purpose        BOOKING | SUBSCRIPTION (plans are now paid through Razorpay)
--   payments.plan_id        the plan a SUBSCRIPTION payment buys (booking_id is NULL then)
--   payments.paid_at        when the payment was confirmed (verify or webhook)
--   bookings.earning_credited_at  worker's share credited to their wallet (once, on completion)
-- All amounts stay in paise.

DROP PROCEDURE IF EXISTS _add_column;
DELIMITER //
CREATE PROCEDURE _add_column(tbl VARCHAR(64), col VARCHAR(64), col_def VARCHAR(255))
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = tbl AND column_name = col
    ) THEN
        SET @s = CONCAT('ALTER TABLE ', tbl, ' ADD COLUMN ', col, ' ', col_def);
        PREPARE stmt FROM @s;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END //
DELIMITER ;

CALL _add_column('payments', 'purpose',  "VARCHAR(20) NOT NULL DEFAULT 'BOOKING'");
CALL _add_column('payments', 'plan_id',  'CHAR(36) NULL');
CALL _add_column('payments', 'paid_at',  'TIMESTAMP NULL');
CALL _add_column('bookings', 'earning_credited_at', 'TIMESTAMP NULL');

DROP PROCEDURE IF EXISTS _add_column;

-- Subscription payments have no booking.
ALTER TABLE payments MODIFY booking_id CHAR(36) NULL;

-- Old flow credited the worker at payment time: mark those bookings so the
-- completion step doesn't credit them a second time.
UPDATE bookings b
SET b.earning_credited_at = COALESCE(b.updated_at, CURRENT_TIMESTAMP),
    b.updated_at = b.updated_at   -- don't let ON UPDATE bump it
WHERE b.earning_credited_at IS NULL
  AND EXISTS (SELECT 1 FROM provider_earnings e WHERE e.booking_id = b.booking_id AND e.type = 'BOOKING');

-- Fill the platform fee for bookings created before it was stored (10% default;
-- edit the 10 if PLATFORM_FEE_PCT differs in this environment).
UPDATE bookings SET platform_fee = FLOOR(total_amount * 10 / 100), updated_at = updated_at
WHERE (platform_fee IS NULL OR platform_fee = 0) AND total_amount > 0;

-- Refunds made before refund_amount was tracked were always full refunds.
UPDATE payments SET refund_amount = amount
WHERE status = 'REFUNDED' AND (refund_amount IS NULL OR refund_amount = 0);
