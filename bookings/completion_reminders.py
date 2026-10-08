"""Reminders for jobs the worker marked done but the customer hasn't confirmed.

A job completes only when BOTH sides tap Done, and nothing auto-completes
(a worker could otherwise leave before finishing). So the customer is nudged
at REMINDER_MINUTES after the worker's Done, and after ADMIN_FLAG_HOURS the
booking is flagged to admin/support, who complete or cancel it.
Runs from location_trigger.py every 15 minutes; flags live in service_snapshot.
"""
from datetime import datetime, timedelta, timezone

from utilities.db_connection import get_table
from bookings.live_tracking import _as_utc, _json, _save_flags_and_push

REMINDER_MINUTES = (30, 120)
ADMIN_FLAG_HOURS = 24


def remind_unconfirmed(conn, now=None, notifier=None) -> list:
    """Returns [(booking_id, action)] for what it did, action = 'reminder_<n>' | 'flagged'."""
    import json
    b = get_table("bookings")
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        b.select()
        .where(b.c.status == "IN_PROGRESS")
        .where(b.c.provider_done_at.isnot(None))
        .where(b.c.customer_done_at.is_(None))
        .where(b.c.completion_disputed_at.is_(None))
    ).fetchall()

    done = []
    for r in rows:
        waited = now - _as_utc(r.provider_done_at)
        snap = _json(r.service_snapshot)

        if waited >= timedelta(hours=ADMIN_FLAG_HOURS):
            if snap.get("completion_admin_flagged"):
                continue
            snap["completion_admin_flagged"] = True
            conn.execute(b.update().where(b.c.booking_id == r.booking_id).values(service_snapshot=json.dumps(snap)))
            try:
                from admin.admin_modal import AdminMaster
                AdminMaster().write_log(conn, None, "COMPLETION_UNCONFIRMED", "booking", r.booking_id,
                                        json.dumps({"provider_done_at": str(r.provider_done_at)}))
            except Exception as e:
                print(f"[Completion] Admin flag log failed (non-fatal): {e}")
            done.append((r.booking_id, "flagged"))
            continue

        # Latest reminder that is due and not sent yet (one push per run at most).
        due = [m for m in REMINDER_MINUTES if waited >= timedelta(minutes=m) and not snap.get(f"completion_reminder_{m}")]
        if not due:
            continue
        for m in due:
            snap[f"completion_reminder_{m}"] = True
        _save_flags_and_push(conn, r, snap, "Is your service finished?",
                             "Your expert marked the job done. Tap \"Work done\" to confirm, or report a problem.",
                             "completion_requested", notifier)
        done.append((r.booking_id, f"reminder_{due[-1]}"))
    return done
