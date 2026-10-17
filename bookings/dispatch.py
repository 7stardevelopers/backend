"""Offering PENDING bookings to workers in rounds, and giving up when nobody accepts.

start()    — first round, called by BookingsService.dispatch (at creation, or once paid).
tick_one() — next round / expiry for one booking; run when the customer's app polls
             GET /bookings/{id} and every minute by dispatch_trigger.py.
tick()     — tick_one() for every open booking.
Rounds are claimed with a conditional update on dispatch_attempts, so two ticks
running at once never push the same round twice.
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from bookings import dispatch_rules as rules
from utilities.db_connection import get_table

OPEN_JOB_STATUSES = ("ACCEPTED", "EN_ROUTE", "IN_PROGRESS")
LIVE_STATUSES = ("EN_ROUTE", "IN_PROGRESS")


def _now(now=None):
    return rules.as_utc(now) if now is not None else datetime.now(timezone.utc)


def _snapshot(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _coords(booking):
    addr = _snapshot(booking.get("address_snapshot"))
    try:
        return float(addr["lat"]), float(addr["lng"])
    except (KeyError, TypeError, ValueError):
        return None


def _service_name(booking):
    return _snapshot(booking.get("service_snapshot")).get("name") or "New job"


# ── Worker schedule clashes ──────────────────────────────────────────────────

def provider_open_jobs(conn, provider_id, exclude_booking_id=None):
    b = get_table("bookings")
    sel = (b.select().where(b.c.provider_id == provider_id)
           .where(b.c.status.in_(OPEN_JOB_STATUSES))
           .where(b.c.provider_done_at.is_(None)))
    if exclude_booking_id:
        sel = sel.where(b.c.booking_id != exclude_booking_id)
    return [dict(r._mapping) for r in conn.execute(sel).fetchall()]


def clashing_job(booking, open_jobs):
    """The worker's job that makes `booking` impossible for them, or None."""
    start, end = rules.job_window(booking.get("scheduled_at"), booking.get("ends_at"))
    for job in open_jobs:
        if booking.get("is_instant") and job["status"] in LIVE_STATUSES:
            return job  # busy on a job right now — can't come immediately
        j_start, j_end = rules.job_window(job.get("scheduled_at"), job.get("ends_at"))
        if rules.windows_clash(start, end, j_start, j_end):
            return job
    return None


# ── Offering ────────────────────────────────────────────────────────────────

def _workers_in_ring(conn, booking, inner_km, outer_km):
    """User ids of available workers for the service between inner_km (exclusive) and
    outer_km, who are free at the booking's time. inner_km None = first round, which
    also includes workers whose location isn't known yet."""
    from providers.provider_matching import haversine
    from providers.providers_modal import ProvidersMaster

    point = _coords(booking)
    out = []
    for p in ProvidersMaster().get_available_for_service(conn, booking["service_id"]):
        if str(p.get("user_id")) == str(booking.get("customer_id")):
            continue
        p_lat, p_lng = p.get("last_lat"), p.get("last_lng")
        if point is None or p_lat is None or p_lng is None:
            if inner_km is not None:
                continue
        else:
            dist = haversine(point[0], point[1], float(p_lat), float(p_lng))
            if dist > outer_km or (inner_km is not None and dist <= inner_km):
                continue
        if clashing_job(booking, provider_open_jobs(conn, p["provider_id"])):
            continue
        out.append(p["user_id"])
    return out


def _record_offers(conn, booking_id, user_ids):
    """Remember who was offered the job so the offer can be withdrawn later."""
    o = get_table("booking_offers")
    now = rules.naive_utc(_now())
    for uid in user_ids:
        conn.execute(o.insert().prefix_with("IGNORE", dialect="mysql")
                     .prefix_with("OR IGNORE", dialect="sqlite")
                     .values(booking_id=booking_id, user_id=uid, offered_at=now))


def _push_offer(conn, notif, booking, user_ids):
    if not user_ids:
        return
    try:
        _record_offers(conn, booking["booking_id"], user_ids)
    except Exception as e:
        print(f"[Dispatch] recording offers failed (non-fatal): {e}")
    from bookings.bookings_service import _job_push_body, _iso_utc
    try:
        if booking.get("is_instant"):
            notif.send_push(
                connection=conn, user_ids=user_ids,
                title=f"⚡ Instant job — {_service_name(booking)}",
                body="A customer needs an expert now. Tap to view and accept.",
                data={"type": "instant_job", "booking_id": booking["booking_id"]},
            )
        else:
            notif.send_push(
                connection=conn, user_ids=user_ids,
                title="New Job Near You",
                body=_job_push_body(booking),
                data={"type": "job_available", "booking_id": booking["booking_id"],
                      "scheduled_at": _iso_utc(booking.get("scheduled_at"))},
            )
    except Exception as e:
        print(f"[Dispatch] offer push failed (non-fatal): {e}")


def withdraw_offers(conn, booking_id, notif, keep_user_id=None):
    """The job is gone (accepted, cancelled or expired): silently tell every other
    worker it was offered to, so it disappears from their app right away."""
    try:
        o = get_table("booking_offers")
        user_ids = [r.user_id for r in conn.execute(
            o.select().where(o.c.booking_id == booking_id)).fetchall()
            if str(r.user_id) != str(keep_user_id)]
        notif.send_silent(conn, user_ids, {"type": "job_taken", "booking_id": booking_id})
    except Exception as e:
        print(f"[Dispatch] withdrawing offers failed (non-fatal): {e}")


def _claim_round(conn, booking, values, now) -> bool:
    """Record a round; False if another tick already took it."""
    b = get_table("bookings")
    attempts = int(booking.get("dispatch_attempts") or 0)
    result = conn.execute(
        b.update().where(b.c.booking_id == booking["booking_id"])
        .where(b.c.status == "PENDING").where(b.c.provider_id.is_(None))
        .where(b.c.dispatch_attempts == attempts)
        .values(dispatch_attempts=attempts + 1, last_dispatched_at=rules.naive_utc(now), **values)
    )
    if result.rowcount != 1:
        return False
    booking.update(values, dispatch_attempts=attempts + 1, last_dispatched_at=rules.naive_utc(now))
    return True


def start(conn, booking, notif, now=None):
    """First round when the booking becomes visible to workers."""
    now = _now(now)
    values = {}
    if booking.get("is_instant"):
        values["dispatch_radius_km"] = rules.INSTANT_ROUNDS[0][1]
        values["expires_at"] = rules.naive_utc(now + timedelta(minutes=rules.INSTANT_TTL_MIN))
    else:
        values["dispatch_radius_km"] = rules.SCHEDULED_RADIUS_KM
    if not _claim_round(conn, booking, values, now):
        return booking
    if booking.get("is_instant"):
        ib = get_table("instant_bookings")
        conn.execute(ib.update().where(ib.c.booking_id == booking["booking_id"])
                     .values(dispatched_at=rules.naive_utc(now)))
    _push_offer(conn, notif, booking, _workers_in_ring(conn, booking, None, values["dispatch_radius_km"]))
    return booking


def tick_one(conn, booking, notif=None, now=None):
    """Advance one PENDING booking: next round, or cancel when its time is up.
    Returns the (possibly updated) booking."""
    from bookings.bookings_service import is_dispatchable
    if (booking.get("status") != "PENDING" or booking.get("provider_id")
            or not booking.get("dispatch_attempts") or not is_dispatchable(booking)):
        return booking  # not offered yet (unpaid "Pay now") or already taken
    now = _now(now)
    if notif is None:
        from notifications.notifications_service import NotificationsService
        notif = NotificationsService()

    expires = rules.as_utc(booking.get("expires_at"))
    if expires is not None and now >= expires:
        return expire(conn, booking, notif)

    if booking.get("is_instant"):
        started = expires - timedelta(minutes=rules.INSTANT_TTL_MIN) if expires else now
        current = int(booking.get("dispatch_radius_km") or 0)
        due = rules.instant_radius_due(started, now)
        if due > current and _claim_round(conn, booking, {"dispatch_radius_km": due}, now):
            _push_offer(conn, notif, booking, _workers_in_ring(conn, booking, current, due))
        return booking

    hours = rules.scheduled_round_due(booking.get("scheduled_at"), booking.get("last_dispatched_at"), now)
    if hours is not None and _claim_round(conn, booking, {}, now):
        radius = int(booking.get("dispatch_radius_km") or rules.SCHEDULED_RADIUS_KM)
        _push_offer(conn, notif, booking, _workers_in_ring(conn, booking, None, radius))
        if hours == 1:
            try:
                notif.send_push(
                    connection=conn, user_ids=[booking["customer_id"]],
                    title="Still looking for your expert",
                    body="No expert has accepted yet. We'll keep trying and let you know.",
                    data={"type": "booking_update", "booking_id": booking["booking_id"], "status": "PENDING"},
                )
            except Exception as e:
                print(f"[Dispatch] still-looking push failed (non-fatal): {e}")
    return booking


def expire(conn, booking, notif):
    """Nobody accepted in time: cancel (refund, give back coupon/coins/quota) and tell the customer."""
    from bookings.bookings_service import BookingsService
    svc = BookingsService()
    try:
        updated = svc._do_cancel(conn, booking, reason=rules.NO_EXPERT_REASON, notify=False)
    except ValueError:
        return svc.modal.read_one(conn, booking["booking_id"])  # accepted/cancelled meanwhile
    if booking.get("is_instant"):
        ib = get_table("instant_bookings")
        conn.execute(ib.update().where(ib.c.booking_id == booking["booking_id"])
                     .values(status="EXPIRED", expired_at=rules.naive_utc(_now())))
    paid = booking.get("payment_status") == "PAID"
    body = ("No expert was free right now." if booking.get("is_instant")
            else "No expert accepted your booking in time.")
    body += " Your payment will be refunded." if paid else " You haven't been charged."
    try:
        notif.send_push(
            connection=conn, user_ids=[booking["customer_id"]],
            title="No expert available", body=body + " Please try another time.",
            data={"type": "booking_expired", "booking_id": booking["booking_id"], "status": "CANCELLED"},
        )
    except Exception as e:
        print(f"[Dispatch] expiry push failed (non-fatal): {e}")
    return updated


def tick(conn, now=None):
    """Every open, offered booking: due rounds and expiries. Returns how many were looked at."""
    rows = conn.execute(text("""
        SELECT booking_id FROM bookings
        WHERE status = 'PENDING' AND provider_id IS NULL
          AND dispatch_attempts > 0 AND expires_at IS NOT NULL
        ORDER BY expires_at ASC
        LIMIT 500
    """)).fetchall()
    from bookings.bookings_modal import BookingsMaster
    from notifications.notifications_service import NotificationsService
    modal, notif = BookingsMaster(), NotificationsService()
    for r in rows:
        try:
            tick_one(conn, modal.read_one(conn, r.booking_id), notif, now)
        except Exception as e:
            print(f"[Dispatch] tick skip {r.booking_id}: {e}")
    return len(rows)
