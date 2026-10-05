from datetime import datetime, timezone


def to_iso_utc(value):
    """ISO-8601 UTC with milliseconds and a 'Z' suffix, e.g. 2026-10-05T10:00:00.123Z.

    The DB stores naive UTC; without the suffix, apps parse it as *local* time
    (5h30 off in India). Non-datetimes are returned unchanged."""
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso_utc(value):
    """Parse an ISO timestamp from a client into naive UTC (for DB comparisons). None if invalid."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
