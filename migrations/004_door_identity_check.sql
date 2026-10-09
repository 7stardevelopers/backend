-- 004: the customer checks the worker's face at the door before the door OTP exists.
-- Run against staging, then production (safe to re-run):
--   mysql -h <host> -u <user> -p <db> < migrations/004_door_identity_check.sql
--
-- Run this BEFORE deploying the backend that uses these columns.
--   identity_confirmed_at  customer tapped "Yes, it's them" — the door OTP is generated now
--   identity_mismatch_at   customer tapped "No, different person" — see identity_reports
-- The door OTP is no longer created at booking time, so a booking made a week
-- ahead gets its code only when the worker is actually at the door.

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

CALL _add_column('bookings', 'identity_confirmed_at', 'TIMESTAMP NULL');
CALL _add_column('bookings', 'identity_mismatch_at',  'TIMESTAMP NULL');

DROP PROCEDURE IF EXISTS _add_column;

-- One row per "No, different person" tap. Admin reviews these on the
-- Identity Reports page: OPEN → ACTION_TAKEN (wrong worker went) or DISMISSED.
CREATE TABLE IF NOT EXISTS identity_reports (
    report_id     CHAR(36)    NOT NULL,
    booking_id    CHAR(36)    NOT NULL,
    provider_id   CHAR(36)    NULL,
    customer_id   CHAR(36)    NOT NULL,
    ticket_id     CHAR(36)    NULL,
    customer_note TEXT        NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    admin_note    TEXT        NULL,
    resolved_by   CHAR(36)    NULL,
    resolved_at   TIMESTAMP   NULL,
    created_at    TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (report_id),
    UNIQUE KEY uq_identity_reports_booking (booking_id),
    KEY idx_identity_reports_status (status, created_at),
    KEY idx_identity_reports_provider (provider_id)
);
