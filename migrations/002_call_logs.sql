-- 002: masked-calling call log details.
-- Run against staging, then production (safe to re-run):
--   mysql -h <host> -u <user> -p <db> < migrations/002_call_logs.sql
--
-- The backend works before this runs (it only writes columns that exist), but
-- call duration / recording / failure reasons are only stored afterwards.

CREATE TABLE IF NOT EXISTS call_logs (
    call_id             CHAR(36) NOT NULL DEFAULT (UUID()),
    booking_id          CHAR(36) NULL,
    initiated_by        CHAR(36) NOT NULL,
    target              VARCHAR(10) NOT NULL,
    exotel_call_sid     VARCHAR(100),
    status              VARCHAR(20) DEFAULT 'INITIATED',
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (call_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id),
    FOREIGN KEY (initiated_by) REFERENCES users(user_id)
);

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

CALL _add_column('call_logs', 'duration_sec',  'INT NULL');
CALL _add_column('call_logs', 'start_time',    'TIMESTAMP NULL');
CALL _add_column('call_logs', 'end_time',      'TIMESTAMP NULL');
CALL _add_column('call_logs', 'recording_url', 'TEXT NULL');
CALL _add_column('call_logs', 'error_message', 'VARCHAR(255) NULL');

CALL _add_index('call_logs', 'idx_call_logs_sid',          'exotel_call_sid');
CALL _add_index('call_logs', 'idx_call_logs_booking',      'booking_id, created_at');
CALL _add_index('call_logs', 'idx_call_logs_initiated_by', 'initiated_by, created_at');

DROP PROCEDURE IF EXISTS _add_column;
DROP PROCEDURE IF EXISTS _add_index;
