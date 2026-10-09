"""
Cancels bookings the customer never paid for. A booking reaches workers only
after payment (see BookingsService.dispatch), so an abandoned checkout would
otherwise sit PENDING forever holding the customer's coupon / coins / quota.
Run by location_trigger every 15 minutes.
"""
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

UNPAID_BOOKING_TTL_MIN = int(os.environ.get("UNPAID_BOOKING_TTL_MIN", "15"))


def expire_unpaid(conn):
    from bookings.bookings_service import BookingsService

    # created_at is stored as naive UTC
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=UNPAID_BOOKING_TTL_MIN)
    rows = conn.execute(text("""
        SELECT booking_id FROM bookings
        WHERE status = 'PENDING'
          AND provider_id IS NULL
          AND (payment_status IS NULL OR payment_status <> 'PAID')
          AND total_amount > 0
          AND created_at < :cutoff
        LIMIT 200
    """), {"cutoff": cutoff}).fetchall()

    svc = BookingsService()
    for r in rows:
        try:
            booking = svc.modal.read_one(conn, r.booking_id)
            if booking.get("payment_status") == "PAID" or booking["status"] != "PENDING":
                continue  # paid / claimed since the SELECT
            # only_unpaid: a payment confirmed after our read wins (no cancel, no refund)
            svc._do_cancel(conn, booking, only_unpaid=True)
        except Exception as e:
            print(f"[UnpaidExpiry] skip {r.booking_id}: {e}")
    return len(rows)

