"""
CloudWatch EventBridge triggers this every 15 minutes.
Finds bookings scheduled within the next 75 minutes and sends live-tracking push notifications,
nudges workers whose location sharing stopped mid-trip, and reminds customers
to confirm jobs the worker marked done (bookings/completion_reminders.py), and
cancels bookings left unpaid (bookings/unpaid_expiry.py).
"""
import traceback

from utilities.env_loader import load_secrets
from utilities.db_connection import get_connection

load_secrets()


def handler(event, context):
    # Each step in its own transaction and try: one failing must not stop the
    # others (e.g. unpaid-booking expiry releasing coupons/coins/quota).
    failed = []
    for name, step in (
        ("upcoming", lambda conn: _process_upcoming_bookings(conn)),
        ("stale_trackers", lambda conn: __import__("bookings.live_tracking", fromlist=["x"]).nudge_stale_trackers(conn)),
        ("completion_reminders", lambda conn: __import__("bookings.completion_reminders", fromlist=["x"]).remind_unconfirmed(conn)),
        ("unpaid_expiry", lambda conn: __import__("bookings.unpaid_expiry", fromlist=["x"]).expire_unpaid(conn)),
    ):
        try:
            with get_connection() as conn:
                step(conn)
        except Exception as e:
            failed.append(name)
            print(f"[LocationTrigger] {name} failed: {e}")
            traceback.print_exc()
    if failed:
        return {"statusCode": 500, "body": f"Failed: {', '.join(failed)}"}
    return {"statusCode": 200, "body": "OK"}


def _process_upcoming_bookings(conn):
    from sqlalchemy import text
    rows = conn.execute(text("""
        SELECT b.booking_id, b.customer_id, b.provider_id, p.user_id AS provider_user_id, b.scheduled_at
        FROM bookings b
        LEFT JOIN providers p ON p.provider_id = b.provider_id
        WHERE b.status = 'ACCEPTED'
          AND b.scheduled_at BETWEEN NOW() AND NOW() + INTERVAL 75 MINUTE
          AND JSON_EXTRACT(b.service_snapshot, '$.location_triggered') IS NULL
    """)).mappings().fetchall()

    if not rows:
        return

    from notifications.notifications_service import NotificationsService
    notif = NotificationsService()

    for row in rows:
        booking_id = row["booking_id"]
        customer_id = row["customer_id"]
        provider_user_id = row["provider_user_id"]
        try:
            if customer_id:
                notif.send_push(
                    connection=conn,
                    user_ids=[str(customer_id)],
                    title="Your expert is on the way!",
                    body="Live tracking is now active. You can track your expert in real-time.",
                    data={"type": "live_tracking_active", "booking_id": str(booking_id)},
                )
            if provider_user_id:
                notif.send_push(
                    connection=conn,
                    user_ids=[str(provider_user_id)],
                    title="Time to head out!",
                    body="Your appointment is in less than 75 minutes. Start navigating now.",
                    data={"type": "navigate_now", "booking_id": str(booking_id)},
                )
            conn.execute(text("""
                UPDATE bookings
                SET service_snapshot = JSON_SET(COALESCE(service_snapshot, JSON_OBJECT()), '$.location_triggered', TRUE)
                WHERE booking_id = :bid
            """), {"bid": str(booking_id)})
        except Exception as e:
            print(f"[LocationTrigger] Failed for booking {booking_id} (non-fatal): {e}")
