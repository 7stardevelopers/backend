"""
EventBridge runs this every minute: offers open bookings to workers again on schedule
(instant: widening radius) and cancels those nobody accepted in time. The same step
also runs whenever the customer's app polls GET /bookings/{id} (bookings/dispatch.py).
"""
import traceback

from utilities.env_loader import load_secrets
from utilities.db_connection import get_connection

load_secrets()


def handler(event, context):
    try:
        from bookings.dispatch import tick
        with get_connection() as conn:
            n = tick(conn)
        return {"statusCode": 200, "body": f"OK ({n})"}
    except Exception as e:
        print(f"[DispatchTrigger] failed: {e}")
        traceback.print_exc()
        return {"statusCode": 500, "body": "Failed"}
