-- 001: uniqueness guards against double-crediting on retries/replays.
-- Run manually against staging, then production:
--   mysql -h <host> -u <user> -p <db> < migrations/001_unique_constraints.sql
--
-- STEP 1 — check for existing duplicates. Each query must return 0 rows before
-- STEP 2 can succeed. If any return rows, clean them up first (they are the
-- result of the bugs this migration protects against).

SELECT booking_id, type, COUNT(*) AS n FROM provider_earnings
GROUP BY booking_id, type HAVING n > 1;

SELECT booking_id, COUNT(*) AS n FROM tips
GROUP BY booking_id HAVING n > 1;

SELECT booking_id, COUNT(*) AS n FROM instant_bookings
GROUP BY booking_id HAVING n > 1;

-- STEP 2 — add the constraints (re-runnable).
DROP PROCEDURE IF EXISTS _add_constraint;
DELIMITER //
CREATE PROCEDURE _add_constraint(tbl VARCHAR(64), cname VARCHAR(64), cdef VARCHAR(255))
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema = DATABASE() AND table_name = tbl AND constraint_name = cname
    ) THEN
        SET @s = CONCAT('ALTER TABLE ', tbl, ' ADD CONSTRAINT ', cname, ' ', cdef);
        PREPARE stmt FROM @s;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END //
DELIMITER ;

CALL _add_constraint('provider_earnings', 'uq_provider_earnings_booking_type', 'UNIQUE (booking_id, type)');
CALL _add_constraint('tips',              'uq_tips_booking',                   'UNIQUE (booking_id)');
CALL _add_constraint('instant_bookings',  'uq_instant_bookings_booking',       'UNIQUE (booking_id)');

DROP PROCEDURE IF EXISTS _add_constraint;
