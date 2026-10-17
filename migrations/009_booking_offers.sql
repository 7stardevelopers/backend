-- 009: Who was offered each job, so the offer can be withdrawn once one worker accepts it.
-- Run against staging, then production (safe to re-run):
--   mysql -h <host> -u <user> -p <db> < migrations/009_booking_offers.sql
--
-- Run this BEFORE deploying the backend that uses this table.
--   booking_offers  one row per (booking, worker user) pushed an offer by dispatch._push_offer.
--                   When a worker accepts, or the booking is cancelled/expires while PENDING,
--                   every other offered worker gets a silent `job_taken` push and the job
--                   disappears from their app.

CREATE TABLE IF NOT EXISTS booking_offers (
    booking_id  CHAR(36) NOT NULL,
    user_id     CHAR(36) NOT NULL,
    offered_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (booking_id, user_id),
    KEY idx_booking_offers_user (user_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
);
