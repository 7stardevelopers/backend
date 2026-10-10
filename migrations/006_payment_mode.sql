-- 006: customer chooses when to pay while booking.
-- Run against staging, then production (safe to re-run):
--   mysql -h <host> -u <user> -p <db> < migrations/006_payment_mode.sql
--
-- Run this BEFORE deploying the backend that uses this column.
--   bookings.payment_mode  PAY_NOW   online at booking; the job reaches workers only once paid
--                          PAY_AFTER cash or online after the job; the job reaches workers at once
-- Existing bookings become PAY_AFTER (how they already behaved).

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

CALL _add_column('bookings', 'payment_mode', "VARCHAR(10) NOT NULL DEFAULT 'PAY_AFTER'");

DROP PROCEDURE IF EXISTS _add_column;
