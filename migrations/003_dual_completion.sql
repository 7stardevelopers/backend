-- 003: a job completes only when BOTH the worker and the customer tap "Done".
-- Run against staging, then production (safe to re-run):
--   mysql -h <host> -u <user> -p <db> < migrations/003_dual_completion.sql
--
-- Run this BEFORE deploying the backend that uses these columns.
--   provider_done_at        worker tapped "Mark as Complete" (with proof photos)
--   customer_done_at        customer tapped "Work done" in their own app
--   completion_disputed_at  customer tapped "Report a problem" (blocks completion)
-- The booking stays IN_PROGRESS until both *_done_at are set, then becomes COMPLETED.

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

CALL _add_column('bookings', 'provider_done_at',       'TIMESTAMP NULL');
CALL _add_column('bookings', 'customer_done_at',       'TIMESTAMP NULL');
CALL _add_column('bookings', 'completion_disputed_at', 'TIMESTAMP NULL');

DROP PROCEDURE IF EXISTS _add_column;
