"""
CloudWatch EventBridge triggers this every 15 minutes.
Finds bookings scheduled within the next 75 minutes and sends live-tracking push notifications.
"""
import json
import os

from utilities.env_loader import load_secrets
from utilities.db_connection import get_connection

load_secrets()


def handler(event, context):
    try:
        with get_connection() as conn:
            _process_upcoming_bookings(conn)
        return {"statusCode": 200, "body": "OK"}
    except Exception as e:
        print(f"[LocationTrigger] Error: {e}")
        return {"statusCode": 500, "body": str(e)}


def _process_upcoming_bookings(conn):
    from sqlalchemy import text
    rows = conn.execute(text("""
        SELECT b.booking_id, b.customer_id, b.provider_id, b.scheduled_at
        FROM bookings b
        WHERE b.status = 'ACCEPTED'
          AND b.scheduled_at BETWEEN NOW() AND NOW() + INTERVAL 75 MINUTE
          AND COALESCE(JSON_EXTRACT(b.service_snapshot, '$.location_triggered'), FALSE) <> TRUE
    """)).fetchall()

    if not rows:
        return

    from notifications.notifications_service import NotificationsService
    notif = NotificationsService()

    for row in rows:
        booking_id = row["booking_id"]
        customer_id = row["customer_id"]
        provider_id = row["provider_id"]
        try:
            if customer_id:
                notif.send_push(
                    connection=conn,
                    user_ids=[str(customer_id)],
                    title="Your expert is on the way!",
                    body="Live tracking is now active. You can track your expert in real-time.",
                    data={"type": "live_tracking_active", "booking_id": str(booking_id)},
                )
            if provider_id:
                notif.send_push(
                    connection=conn,
                    user_ids=[str(provider_id)],
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
