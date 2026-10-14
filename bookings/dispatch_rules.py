"""When a booking may be placed and how it is offered to workers.

INSTANT ("Now") — the job is offered at once to online workers in a small radius that
widens over a few minutes; nobody accepting by INSTANT_TTL_MIN cancels it ("no expert").
SCHEDULED — any slot from MIN_LEAD_MIN ahead up to MAX_ADVANCE_DAYS, inside working
hours (IST). It is offered at booking and again REOFFER_HOURS_BEFORE the slot; nobody
accepting by EXPIRE_BEFORE_MIN before the slot cancels it.
All times here are aware UTC unless named *_ist.
"""
import os
from datetime import datetime, timedelta, timezone, date as date_cls

IST = timezone(timedelta(hours=5, minutes=30))

MIN_LEAD_MIN = int(os.environ.get("MIN_LEAD_MIN", "60"))
MAX_ADVANCE_DAYS = int(os.environ.get("MAX_ADVANCE_DAYS", "14"))
SLOT_START_HOUR = int(os.environ.get("SLOT_START_HOUR", "7"))     # first slot 07:00 IST
SLOT_END_HOUR = int(os.environ.get("SLOT_END_HOUR", "21"))        # last slot starts before 21:00 IST
SLOT_STEP_MIN = 30

# Instant: (minutes after the search started, radius km). Each step offers the job
# to the workers in the new ring only.
INSTANT_ROUNDS = [(0, 5), (2, 8), (5, 12)]
INSTANT_TTL_MIN = int(os.environ.get("INSTANT_TTL_MIN", "10"))
INSTANT_ETA_MIN = 60

SCHEDULED_RADIUS_KM = 20
REOFFER_HOURS_BEFORE = (24, 3, 1)   # the 1 h round also tells the customer we're still looking
EXPIRE_BEFORE_MIN = 30

DEFAULT_JOB_MIN = 60                # job length when its items don't say
CLASH_BUFFER_MIN = 30               # travel time between two jobs of one worker
FREE_CANCEL_BEFORE_H = 2            # scheduled: free cancel until this long before the slot

NO_EXPERT_REASON = "NO_EXPERT"


def as_utc(value):
    """Aware UTC datetime from a naive-UTC/aware datetime or ISO string; None if empty."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def naive_utc(value):
    """What the DB stores (naive UTC)."""
    return as_utc(value).replace(tzinfo=None)


def _utcnow(now=None):
    return as_utc(now) if now is not None else datetime.now(timezone.utc)


def _hours_label():
    def fmt(h):
        return f"{(h - 1) % 12 + 1} {'AM' if h < 12 else 'PM'}"
    return f"{fmt(SLOT_START_HOUR)}–{fmt(SLOT_END_HOUR)}"


def in_working_hours(when) -> bool:
    local = as_utc(when).astimezone(IST)
    minutes = local.hour * 60 + local.minute
    return SLOT_START_HOUR * 60 <= minutes < SLOT_END_HOUR * 60


def validate_slot(when, now=None):
    """Raise ValueError (shown to the customer) if a scheduled time can't be booked."""
    when, now = as_utc(when), _utcnow(now)
    if when < now + timedelta(minutes=MIN_LEAD_MIN):
        raise ValueError(f"Please pick a time at least {MIN_LEAD_MIN} minutes from now, "
                         "or book Now for the earliest expert")
    if when > now + timedelta(days=MAX_ADVANCE_DAYS):
        raise ValueError(f"Bookings can be made up to {MAX_ADVANCE_DAYS} days ahead")
    if not in_working_hours(when):
        raise ValueError(f"Please pick a time between {_hours_label()}")


def instant_open(now=None) -> bool:
    """Instant bookings are taken during working hours (with time left to arrive)."""
    now = _utcnow(now)
    return in_working_hours(now) and in_working_hours(now + timedelta(minutes=INSTANT_ETA_MIN))


def instant_closed_message() -> str:
    return f"Instant booking is available {_hours_label()}. Please schedule a time instead."


def slots_for(day, now=None) -> list:
    """Every slot of an IST calendar day with whether it can be booked."""
    now = _utcnow(now)
    if isinstance(day, str):
        day = date_cls.fromisoformat(day)
    out = []
    start = datetime(day.year, day.month, day.day, SLOT_START_HOUR, tzinfo=IST)
    end = datetime(day.year, day.month, day.day, SLOT_END_HOUR, tzinfo=IST)
    t = start
    while t < end:
        slot = {"time": t.strftime("%H:%M"), "scheduled_at": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "available": True}
        try:
            validate_slot(t, now)
        except ValueError:
            slot["available"] = False
            slot["reason"] = "too_soon" if t < now + timedelta(minutes=MIN_LEAD_MIN) else "too_far"
        out.append(slot)
        t += timedelta(minutes=SLOT_STEP_MIN)
    return out


def next_open_slot(now=None):
    """Earliest bookable slot (aware UTC) — used by tests and the slots API."""
    now = _utcnow(now)
    day = now.astimezone(IST).date()
    for _ in range(MAX_ADVANCE_DAYS + 1):
        for s in slots_for(day, now):
            if s["available"]:
                return as_utc(s["scheduled_at"])
        day += timedelta(days=1)
    return None


def instant_radius_due(started_at, now=None) -> int:
    """Radius (km) an instant search should have reached by now."""
    elapsed = (_utcnow(now) - as_utc(started_at)).total_seconds() / 60
    radius = INSTANT_ROUNDS[0][1]
    for after_min, km in INSTANT_ROUNDS:
        if elapsed >= after_min:
            radius = km
    return radius


def scheduled_round_due(scheduled_at, last_dispatched_at, now=None):
    """Hours-before mark of a scheduled re-offer that is due now (None if none)."""
    now, slot = _utcnow(now), as_utc(scheduled_at)
    last = as_utc(last_dispatched_at)
    for hours in sorted(REOFFER_HOURS_BEFORE):          # closest to the slot first
        mark = slot - timedelta(hours=hours)
        if now >= mark and (last is None or last < mark):
            return hours
    return None


def job_window(scheduled_at, ends_at=None):
    start = as_utc(scheduled_at)
    end = as_utc(ends_at) or (start + timedelta(minutes=DEFAULT_JOB_MIN))
    return start, end


def windows_clash(a_start, a_end, b_start, b_end) -> bool:
    """Two jobs overlap once travel time between them is counted."""
    buf = timedelta(minutes=CLASH_BUFFER_MIN)
    return a_start < b_end + buf and b_start < a_end + buf
