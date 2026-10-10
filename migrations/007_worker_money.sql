-- 007: Rapido-style worker money — fee on cash jobs, cancellation charge, scheduled payouts.
-- Run against staging, then production (safe to re-run):
--   mysql -h <host> -u <user> -p <db> < migrations/007_worker_money.sql
--
-- Run this BEFORE deploying the backend that uses these columns.
--   bookings.accepted_at            when a worker claimed the job (starts the free-cancel grace period)
--   bookings.cancellation_fee       fee charged when the customer cancelled late (paise)
--   bookings.cancel_fee_status      DUE (owed by the customer) | COLLECTING (on their next booking) | COLLECTED
--   bookings.cancel_fee_booking_id  the later booking whose total carries this fee
--   bookings.dues_collected         earlier cancellation fees included in this booking's total (paise)
-- New provider_earnings.type values (no schema change): CASH_FEE, CASH_FEE_REVERSAL, CANCEL_FEE, DUES_PAID.
-- New payments.purpose value: WORKER_DUES (a worker paying what they owe from cash jobs).

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

CALL _add_column('bookings', 'accepted_at',           'TIMESTAMP NULL');
CALL _add_column('bookings', 'cancellation_fee',      'INT NOT NULL DEFAULT 0');
CALL _add_column('bookings', 'cancel_fee_status',     'VARCHAR(12) NULL');
CALL _add_column('bookings', 'cancel_fee_booking_id', 'CHAR(36) NULL');
CALL _add_column('bookings', 'dues_collected',        'INT NOT NULL DEFAULT 0');

DROP PROCEDURE IF EXISTS _add_column;
