import hmac
import os
from datetime import datetime, timedelta, timezone

from calls import exotel_client
from calls.calls_modal import CallsMaster
from calls.calls_validator import InitiateCallSchema, CallStatusCallbackSchema
from providers.providers_modal import ProvidersMaster
from utilities.auth_tokens import is_deployed, is_production
from utilities.db_connection import get_table
from utilities.redis_connection import get_redis

ACTIVE_STATUSES = ("ACCEPTED", "EN_ROUTE", "IN_PROGRESS")
CALL_COOLDOWN_SEC = 20          # between two calls by the same user on the same booking
CALL_WINDOW_SEC = 15 * 60
CALL_WINDOW_MAX = 6             # calls per user per booking per window

# Exotel terminal statuses → what we store
EXOTEL_STATUS_MAP = {
    "completed": "COMPLETED",
    "busy": "BUSY",
    "no-answer": "NO-ANSWER",
    "failed": "FAILED",
    "canceled": "CANCELED",
    "cancelled": "CANCELED",
    "in-progress": "IN_PROGRESS",
    "ringing": "RINGING",
    "queued": "INITIATED",
}
IST = timezone(timedelta(hours=5, minutes=30))

GENERIC_FAILURE = "Couldn't connect the call right now. Please try again in a moment."


class CallsService:
    def __init__(self):
        self.modal = CallsMaster()

    # ── POST /calls/initiate ──────────────────────────────────────────

    def initiate_call(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        data = InitiateCallSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})

        if not exotel_client.is_configured():
            print("[Calls] Exotel not configured — refusing call")
            raise ValueError("Calling is not available right now. Please use chat instead.")

        if data.booking_id:
            caller_number, callee_number = self._booking_numbers(connection, data, user_id, role)
        elif data.target_user_id:
            caller_number, callee_number = self._direct_numbers(connection, data, user_id, role)
        else:
            raise ValueError("booking_id or target_user_id is required")

        _check_rate_limit(user_id, data.booking_id or data.target_user_id)

        log = {
            "booking_id": data.booking_id,
            "initiated_by": user_id,
            "target": (data.target.upper() if data.target else "DIRECT"),
        }
        try:
            sid = exotel_client.connect_call(caller_number, callee_number)
        except ValueError:
            # normalize_number rejected a stored phone — data problem, not Exotel
            self._log_failure(log, "invalid phone number on file")
            raise ValueError("This call can't be placed — a phone number on file is invalid.")
        except exotel_client.ExotelError as e:
            print(f"[Calls] Exotel error: {e}")   # message never contains credentials
            self._log_failure(log, str(e))
            if is_production():
                raise ValueError(GENERIC_FAILURE)
            # Outside production show Exotel's reason so setup problems can be
            # fixed from the app (it never includes credentials).
            raise ValueError(f"{GENERIC_FAILURE} [{e}]")

        call_row = self.modal.create(connection, {**log, "exotel_call_sid": sid, "status": "INITIATED"})
        return "success", {
            "message": "Connecting your call. Please answer the incoming call — your number stays private.",
            "call_id": call_row["call_id"],
        }

    def _booking_numbers(self, connection, data, user_id, role):
        if not data.target:
            raise ValueError("target ('customer' or 'provider') is required for a booking call")

        bookings_t = get_table("bookings")
        booking = connection.execute(
            bookings_t.select().where(bookings_t.c.booking_id == data.booking_id)
        ).mappings().fetchone()
        if not booking:
            raise ValueError("Booking not found")
        if not booking["provider_id"]:
            raise ValueError("No expert has been assigned to this booking yet")

        provider = ProvidersMaster().find_by_id(connection, booking["provider_id"])
        if not provider:
            raise ValueError("Provider not found")

        if role == "CUSTOMER":
            if str(booking["customer_id"]) != str(user_id):
                raise PermissionError("Not your booking")
            if data.target != "provider":
                raise PermissionError("Customers may only call the assigned provider")
        elif role == "PROVIDER":
            if str(provider["user_id"]) != str(user_id):
                raise PermissionError("Not your booking")
            if data.target != "customer":
                raise PermissionError("Providers may only call the customer")
        elif role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Not authorized to place calls")

        if booking["status"] not in ACTIVE_STATUSES and role not in ("ADMIN", "SUPPORT"):
            raise ValueError("Calls are only available while the booking is active")

        customer_phone = _phone_of(connection, booking["customer_id"])
        provider_phone = _phone_of(connection, provider["user_id"])
        if not customer_phone or not provider_phone:
            raise ValueError("This call can't be placed — a phone number is missing.")

        if role in ("ADMIN", "SUPPORT"):
            # Admin/support: their own phone rings first, then the chosen party
            admin_phone = _phone_of(connection, user_id)
            if not admin_phone:
                raise ValueError("Add a phone number to your admin account to place calls")
            return admin_phone, (provider_phone if data.target == "provider" else customer_phone)
        if data.target == "provider":
            return customer_phone, provider_phone
        return provider_phone, customer_phone

    def _direct_numbers(self, connection, data, user_id, role):
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Only admin/support may call a user directly")
        target_phone = _phone_of(connection, data.target_user_id)
        if target_phone is None:
            raise ValueError("Target user not found or has no phone number")
        admin_phone = _phone_of(connection, user_id)
        if not admin_phone:
            raise ValueError("Add a phone number to your admin account to place calls")
        return admin_phone, target_phone

    def _log_failure(self, log: dict, reason: str):
        try:
            self.modal.create_committed({**log, "status": "FAILED", "error_message": reason[:255]})
        except Exception as e:
            print(f"[Calls] Could not record failed call (non-fatal): {e}")

    # ── POST /calls/status-callback (public, Exotel) ──────────────────

    def status_callback(self, obj, connection):
        secret = os.environ.get("EXOTEL_CALLBACK_SECRET", "")
        if secret:
            if not hmac.compare_digest(str(obj.get("token", "")), secret):
                raise PermissionError("Invalid callback token")
        elif is_deployed():
            print("[Calls] EXOTEL_CALLBACK_SECRET not set — rejecting status callback")
            raise PermissionError("Callback not configured")
        else:
            print("[Calls] WARNING: EXOTEL_CALLBACK_SECRET not set — accepting callback (local dev)")

        data = CallStatusCallbackSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        raw_status = data.Status or data.DialCallStatus or _status_from_legs(data.Legs) or ""
        fields = {"status": EXOTEL_STATUS_MAP.get(raw_status.strip().lower(), raw_status.upper()[:20] or "UNKNOWN")}
        duration = _to_int(data.ConversationDuration)
        if duration is not None:
            fields["duration_sec"] = duration
        if _ist_to_utc(data.StartTime):
            fields["start_time"] = _ist_to_utc(data.StartTime)
        if _ist_to_utc(data.EndTime):
            fields["end_time"] = _ist_to_utc(data.EndTime)
        if data.RecordingUrl:
            fields["recording_url"] = data.RecordingUrl

        if not self.modal.update_by_sid(connection, data.CallSid, fields):
            print(f"[Calls] Status callback for unknown CallSid {data.CallSid}")
        # Always 200 so Exotel doesn't keep retrying
        return "success", {"message": "OK"}

    # ── GET /calls/{id} (participants) ────────────────────────────────

    def get_status(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        call = self.modal.find_by_id(connection, obj.get("id"))
        if not call:
            raise ValueError("Call not found")
        if role not in ("ADMIN", "SUPPORT") and str(call["initiated_by"]) != str(user_id):
            if not (call.get("booking_id") and _is_booking_participant(connection, call["booking_id"], user_id)):
                raise PermissionError("Access denied")
        return "success", {
            "call_id": call["call_id"],
            "booking_id": call.get("booking_id"),
            "status": call.get("status"),
            "duration_sec": call.get("duration_sec"),
            "created_at": call.get("created_at"),
        }

    # ── GET /admin/calls ──────────────────────────────────────────────

    def admin_list(self, obj, connection):
        obj.pop("_user_id", None)
        role = obj.pop("_role", None)
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Admin or Support role required")
        result = self.modal.list_admin(
            connection,
            booking_id=obj.get("booking_id"),
            status=obj.get("status"),
            user_id=obj.get("user_id"),
            page=int(obj.get("page", 1)),
            per_page=int(obj.get("per_page", 20)),
        )
        return "success", result


# ── helpers ───────────────────────────────────────────────────────────

def _phone_of(connection, user_id):
    users_t = get_table("users")
    row = connection.execute(
        users_t.select().where(users_t.c.user_id == user_id)
    ).mappings().fetchone()
    return (row["phone"] or None) if row else None


def _check_rate_limit(user_id, scope_id):
    """Cooldown between taps + cap per window. Each call costs money and can be
    used to harass, so this is enforced server-side regardless of the app."""
    r = get_redis()
    cooldown_key = f"call_cd:{user_id}:{scope_id}"
    if r.get(cooldown_key):
        raise ValueError("Please wait a few seconds before calling again.")
    window_key = f"call_rate:{user_id}:{scope_id}"
    count = r.incr(window_key)
    if r.ttl(window_key) < 0:
        r.expire(window_key, CALL_WINDOW_SEC)
    if count > CALL_WINDOW_MAX:
        raise ValueError("Too many call attempts. Please try again later or use chat.")
    r.setex(cooldown_key, CALL_COOLDOWN_SEC, "1")


def _is_booking_participant(connection, booking_id, user_id) -> bool:
    from sqlalchemy import text
    row = connection.execute(text("""
        SELECT 1 FROM bookings b
        LEFT JOIN providers p ON p.provider_id = b.provider_id
        WHERE b.booking_id = :bid AND (b.customer_id = :uid OR p.user_id = :uid)
    """), {"bid": booking_id, "uid": user_id}).fetchone()
    return row is not None


def _status_from_legs(legs):
    """Fallback: overall status from the second leg (the callee)."""
    if not legs:
        return None
    try:
        last = legs[-1]
        return (last.get("Status") if isinstance(last, dict) else None)
    except Exception:
        return None


def _to_int(value):
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ist_to_utc(value):
    """Exotel reports times in IST as 'YYYY-MM-DD HH:MM:SS'. DB stores naive UTC."""
    if not value:
        return None
    try:
        local = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        return local.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None
