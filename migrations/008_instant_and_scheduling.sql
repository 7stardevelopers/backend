-- 008: Instant ("Now") and scheduled booking — re-dispatch, expiry, clash check.
-- Run against staging, then production (safe to re-run):
--   mysql -h <host> -u <user> -p <db> < migrations/008_instant_and_scheduling.sql
--
-- Run this BEFORE deploying the backend that uses these columns.
--   bookings.expires_at          unaccepted booking is cancelled ("no expert found") after this
--   bookings.dispatch_attempts   how many times the job was offered to workers
--   bookings.last_dispatched_at  when it was last offered (re-offer timing)
--   bookings.dispatch_radius_km  current search radius; widens on each re-offer
--   bookings.ends_at             scheduled_at + job duration (worker clash check on accept)
--   instant_bookings.expired_at  when an instant search gave up
-- New instant_bookings.status values (no schema change): EXPIRED, CANCELLED.

DROP PROCEDURE IF EXISTS _add_column;
DROP PROCEDURE IF EXISTS _add_index;
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

CALL _add_column('bookings', 'expires_at',         'TIMESTAMP NULL');
CALL _add_column('bookings', 'dispatch_attempts',  'INT NOT NULL DEFAULT 0');
CALL _add_column('bookings', 'last_dispatched_at', 'TIMESTAMP NULL');
CALL _add_column('bookings', 'dispatch_radius_km', 'INT NULL');
CALL _add_column('bookings', 'ends_at',            'TIMESTAMP NULL');
CALL _add_column('instant_bookings', 'expired_at', 'TIMESTAMP NULL');

CALL _add_index('bookings', 'idx_bookings_status_expires',         'status, expires_at');
CALL _add_index('bookings', 'idx_bookings_provider_status_sched',  'provider_id, status, scheduled_at');

DROP PROCEDURE IF EXISTS _add_column;
DROP PROCEDURE IF EXISTS _add_index;
