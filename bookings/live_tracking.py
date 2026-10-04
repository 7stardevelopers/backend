"""Which bookings may see their provider's live location.

Mirrors needsTracking() in worker/utils/location.js: EN_ROUTE / IN_PROGRESS
always, ACCEPTED only within PRE_JOB_WINDOW_MIN of the slot. Anything else
(a booking for tomorrow, a completed or cancelled one) must never receive the
provider's position — they may be at another customer's home.

One provider does several bookings a day: while they are EN_ROUTE to / working
at another customer, an ACCEPTED booking in its pre-job window is "busy" and
gets no location either (that customer would otherwise watch them inside
someone else's home).
"""
from datetime import datetime, timedelta, timezone

from utilities.db_connection import get_table

LIVE_STATUSES = ("EN_ROUTE", "IN_PROGRESS")
PRE_JOB_WINDOW_MIN = 60


def _as_utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    # MySQL DATETIME comes back naive; the app stores UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _getter(booking):
    return booking.get if isinstance(booking, dict) else lambda k: getattr(booking, k, None)


def _in_window(booking, now=None) -> bool:
    """Status/time rule only (no check of the provider's other bookings)."""
    get = _getter(booking)
    status = get("status")
    if status in LIVE_STATUSES:
        return True
    if status != "ACCEPTED":
        return False
    scheduled = _as_utc(get("scheduled_at"))
    if scheduled is None:
        return False
    now = now or datetime.now(timezone.utc)
    return scheduled - now <= timedelta(minutes=PRE_JOB_WINDOW_MIN)


def provider_busy_elsewhere(conn, booking) -> bool:
    """True when the booking is ACCEPTED and its provider is EN_ROUTE / IN_PROGRESS on another one."""
    get = _getter(booking)
    if get("status") != "ACCEPTED" or not get("provider_id"):
        return False
    b = get_table("bookings")
    row = conn.execute(
        b.select()
        .where(b.c.provider_id == get("provider_id"))
        .where(b.c.booking_id != get("booking_id"))
        .where(b.c.status.in_(LIVE_STATUSES))
        .limit(1)
    ).fetchone()
    return row is not None


def is_live_tracking(booking, now=None, conn=None) -> bool:
    """booking is a dict or row with status and scheduled_at (and provider_id /
    booking_id for the busy check, which only runs when conn is given)."""
    if not _in_window(booking, now):
        return False
    return conn is None or not provider_busy_elsewhere(conn, booking)


def live_tracking_bookings(conn, provider_id, now=None) -> list:
    """Bookings of provider_id whose customer may currently see the provider's location."""
    b = get_table("bookings")
    rows = conn.execute(
        b.select()
        .where(b.c.provider_id == provider_id)
        .where(b.c.status.in_(("ACCEPTED",) + LIVE_STATUSES))
    ).fetchall()
    rows = [r for r in rows if _in_window(r, now)]
    if any(r.status in LIVE_STATUSES for r in rows):
        rows = [r for r in rows if r.status in LIVE_STATUSES]
    return rows


# ── Arrival alerts ──────────────────────────────────────────────────────────
ARRIVING_MIN = 2       # road ETA at or under this → "arriving" push
ARRIVING_KM = 0.5      # …or this close in a straight line
ARRIVED_KM = 0.1


def _json(value):
    import json
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return dict(value or {})


def booking_destination(booking):
    """(lat, lng) of the booking address, or None."""
    get = booking.get if isinstance(booking, dict) else lambda k: getattr(booking, k, None)
    addr = _json(get("address_snapshot"))
    if addr.get("lat") is None or addr.get("lng") is None:
        return None
    return float(addr["lat"]), float(addr["lng"])


def check_arrival(conn, booking, lat, lng, eta=None, notifier=None):
    """Sends the one-time "arriving" / "arrived" pushes for an EN_ROUTE booking.
    Flags live in service_snapshot (same pattern as location_trigger.py).
    Returns the stage that fired ("arriving" | "arrived") or None."""
    if booking.status != "EN_ROUTE":
        return None
    dest = booking_destination(booking)
    if dest is None:
        return None
    from providers.provider_matching import haversine
    km = haversine(lat, lng, dest[0], dest[1])
    snap = _json(booking.service_snapshot)

    if km <= ARRIVED_KM and not snap.get("arrived_notified"):
        stage, title, body = "arrived", "Your expert has arrived", "Keep your door OTP ready."
        snap["arrived_notified"] = snap["arriving_notified"] = True
    elif not snap.get("arriving_notified") and (
        km <= ARRIVING_KM or (eta and eta.get("duration_min") is not None and eta["duration_min"] <= ARRIVING_MIN)
    ):
        stage, title, body = "arriving", "Your expert is arriving", "They're about 2 minutes away."
        snap["arriving_notified"] = True
    else:
        return None
    _save_flags_and_push(conn, booking, snap, title, body, f"expert_{stage}", notifier)
    return stage


def _save_flags_and_push(conn, booking, snap, title, body, push_type, notifier=None, user_id=None):
    """Persist service_snapshot flags first (so a failed push is never retried
    every few seconds), then notify the customer (or user_id). Push is non-fatal."""
    import json
    b = get_table("bookings")
    conn.execute(b.update().where(b.c.booking_id == booking.booking_id).values(service_snapshot=json.dumps(snap)))
    try:
        if notifier is None:
            from notifications.notifications_service import NotificationsService
            notifier = NotificationsService()
        notifier.send_push(
            connection=conn,
            user_ids=[str(user_id or booking.customer_id)],
            title=title,
            body=body,
            data={"type": push_type, "booking_id": str(booking.booking_id)},
        )
    except Exception as e:
        print(f"[LiveTracking] {push_type} push failed (non-fatal): {e}")


# ── Running late ────────────────────────────────────────────────────────────
LATE_GRACE_MIN = 10
IST = timezone(timedelta(hours=5, minutes=30))


def check_late(conn, booking, eta, now=None, notifier=None) -> bool:
    """One-time "running late" push when the road ETA lands more than
    LATE_GRACE_MIN after the booked slot. Home-service bookings have a slot, so
    lateness is measured against it (not against the first ETA like a ride)."""
    if booking.status != "EN_ROUTE" or not eta or eta.get("duration_min") is None:
        return False
    scheduled = _as_utc(booking.scheduled_at)
    if scheduled is None:
        return False
    now = now or datetime.now(timezone.utc)
    arrive = now + timedelta(minutes=eta["duration_min"])
    if arrive <= scheduled + timedelta(minutes=LATE_GRACE_MIN):
        return False
    snap = _json(booking.service_snapshot)
    if snap.get("late_notified") or snap.get("arriving_notified"):
        return False
    snap["late_notified"] = True
    when = arrive.astimezone(IST).strftime("%I:%M %p").lstrip("0")
    _save_flags_and_push(conn, booking, snap, "Your expert is running late",
                         f"Sorry for the delay — they'll arrive around {when}.", "expert_late", notifier)
    return True


# ── Lost signal mid-trip ────────────────────────────────────────────────────
STALE_NUDGE_MIN = 10


def nudge_stale_trackers(conn, now=None, notifier=None) -> list:
    """Pushes the worker once per EN_ROUTE booking whose last location is older
    than STALE_NUDGE_MIN (phone killed the task, no signal, app force-quit) —
    opening the app restarts sharing via reconcileTracking. Returns booking ids."""
    b, p, pl = get_table("bookings"), get_table("providers"), get_table("provider_locations")
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        b.select().add_columns(p.c.user_id.label("provider_user_id"), pl.c.updated_at.label("loc_at"))
        .select_from(b.join(p, p.c.provider_id == b.c.provider_id).outerjoin(pl, pl.c.provider_id == b.c.provider_id))
        .where(b.c.status == "EN_ROUTE")
    ).fetchall()
    nudged = []
    for r in rows:
        loc_at = _as_utc(r.loc_at)
        if loc_at is not None and now - loc_at < timedelta(minutes=STALE_NUDGE_MIN):
            continue
        snap = _json(r.service_snapshot)
        if snap.get("stale_nudged"):
            continue
        snap["stale_nudged"] = True
        _save_flags_and_push(conn, r, snap, "Location sharing stopped",
                             "Open the app so your customer can track you.", "location_stale",
                             notifier, user_id=r.provider_user_id)
        nudged.append(r.booking_id)
    return nudged
