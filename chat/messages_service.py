from chat.messages_modal import MessagesMaster
from chat.messages_validator import SendMessageSchema
from bookings.bookings_modal import BookingsMaster
from utilities.contact_masking import mask_contact_info
from utilities.time_format import to_iso_utc, parse_iso_utc

# Chat is open while an expert is assigned and the job isn't finished.
CHAT_OPEN_STATUSES = ("ACCEPTED", "EN_ROUTE", "IN_PROGRESS")
# Once the booking ends the thread is hidden from both participants (like
# Rapido). Messages are kept — admin/support can still read them.
CHAT_ENDED_STATUSES = ("COMPLETED", "CANCELLED", "REJECTED")
CHAT_ENDED_MESSAGE = "Chat has ended for this booking"
CHAT_RATE_PER_MIN = 20
PUSH_PREVIEW_CHARS = 120


def _provider_user_id(connection, booking_provider_id):
    """Resolve providers.provider_id → users.user_id."""
    if not booking_provider_id:
        return None
    from providers.providers_modal import ProvidersMaster
    prov = ProvidersMaster().find_by_id(connection, booking_provider_id)
    return str(prov["user_id"]) if prov else None


def serialize_message(msg: dict) -> dict:
    """Wire format shared by REST and WebSocket: every timestamp is ISO-8601 UTC ('…Z')."""
    return {k: to_iso_utc(v) for k, v in msg.items()}


def chat_frame(msg: dict, client_id=None) -> dict:
    """WebSocket frame for a new message.

    `message_type` is the *frame* type ("chat"); the message's own kind lives in
    `content_type`. (Spreading the message after "chat" used to overwrite it with
    "text", so the worker app dropped every live message.)"""
    frame = serialize_message(msg)
    frame["content_type"] = msg.get("message_type", "text")
    frame["message_type"] = "chat"
    if client_id:
        frame["client_id"] = client_id
    return frame


class MessagesService:
    def __init__(self):
        self.modal = MessagesMaster()
        self.booking_modal = BookingsMaster()

    # ── participants ──────────────────────────────────────────────────

    def _participants(self, connection, booking_id):
        booking = self.booking_modal.read_one(connection, booking_id)
        customer_id = str(booking["customer_id"])
        prov_user_id = _provider_user_id(connection, booking.get("provider_id"))
        return booking, customer_id, prov_user_id

    # ── POST /bookings/{id}/messages  (also WS "sendMessage") ─────────

    def send_message(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        obj["booking_id"] = booking_id
        data = SendMessageSchema(**{k: v for k, v in obj.items() if not k.startswith("_") and k != "id"})

        booking, customer_id, prov_user_id = self._participants(connection, data.booking_id)
        if str(user_id) == customer_id:
            if not prov_user_id:
                raise ValueError("You can chat once an expert accepts your booking")
            to_id = prov_user_id
        elif prov_user_id and str(user_id) == prov_user_id:
            to_id = customer_id
        else:
            raise PermissionError("You are not a participant of this booking")

        if booking["status"] not in CHAT_OPEN_STATUSES:
            raise ValueError("This chat is closed because the booking is " + str(booking["status"]).lower().replace("_", " "))

        _check_rate_limit(user_id, data.booking_id)

        text, was_masked = mask_contact_info(data.text.strip())
        if not text:
            raise ValueError("Message can't be empty")
        if was_masked:
            print(f"[Chat] Contact info masked in booking {data.booking_id} (sender {user_id})")

        msg = self.modal.send(connection, user_id, to_id, data.booking_id, text, data.message_type)

        delivered = self._push(connection, to_id, chat_frame(msg))
        if delivered:
            msg["delivered_at"] = self.modal.mark_delivered(connection, msg["message_id"])
        # Sender's own devices get the saved copy (with client_id) so the
        # optimistic "sending…" bubble can be reconciled exactly.
        self._push(connection, user_id, chat_frame(msg, data.client_id))

        self._notify(connection, user_id, to_id, data.booking_id, msg)

        out = serialize_message(msg)
        if data.client_id:
            out["client_id"] = data.client_id
        out["masked"] = was_masked
        return "created", out

    # ── GET /bookings/{id}/messages ───────────────────────────────────

    def list_messages(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")

        booking, customer_id, prov_user_id = self._participants(connection, booking_id)
        is_customer = str(user_id) == customer_id
        is_provider = bool(prov_user_id) and str(user_id) == prov_user_id
        if not is_customer and not is_provider and role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Access denied")
        if (is_customer or is_provider) and booking["status"] in CHAT_ENDED_STATUSES:
            raise PermissionError(CHAT_ENDED_MESSAGE)

        since = None
        if obj.get("since"):
            since = parse_iso_utc(obj["since"])
            if since is None:
                raise ValueError("since must be an ISO-8601 timestamp")

        # Only an explicit ?mark_seen=1 marks messages seen — background polls
        # must not flip read receipts.
        if (is_customer or is_provider) and str(obj.get("mark_seen", "")).lower() in ("1", "true"):
            self._mark_seen_and_notify(connection, booking_id, user_id,
                                       prov_user_id if is_customer else customer_id)

        messages = self.modal.list_for_booking(
            connection, booking_id,
            limit=obj.get("limit") or 50,
            before_id=obj.get("before"),
            since=since,
        )
        return "success", [serialize_message(m) for m in messages]

    # ── POST /bookings/{id}/messages/seen  (also WS "markSeen") ───────

    def mark_seen(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking, customer_id, prov_user_id = self._participants(connection, booking_id)
        if str(user_id) == customer_id:
            other = prov_user_id
        elif prov_user_id and str(user_id) == prov_user_id:
            other = customer_id
        else:
            raise PermissionError("You are not a participant of this booking")
        if booking["status"] in CHAT_ENDED_STATUSES:
            return "success", {"marked": 0, "seen_at": None}
        count, seen_at = self._mark_seen_and_notify(connection, booking_id, user_id, other)
        return "success", {"marked": count, "seen_at": to_iso_utc(seen_at)}

    def _mark_seen_and_notify(self, connection, booking_id, user_id, other_user_id):
        count, seen_at = self.modal.mark_seen(connection, booking_id, user_id)
        if count and other_user_id:
            self._push(connection, other_user_id, {
                "message_type": "chat_seen",
                "booking_id": booking_id,
                "seen_by": user_id,
                "seen_at": to_iso_utc(seen_at),
            })
        return count, seen_at

    # ── helpers ───────────────────────────────────────────────────────

    def _push(self, connection, user_id, payload) -> int:
        from utilities.ws_push import push_to_user
        try:
            return push_to_user(connection, user_id, payload) or 0
        except Exception as e:
            print(f"[Chat] WS delivery failed (non-fatal): {e}")
            return 0

    def _notify(self, connection, from_id, to_id, booking_id, msg):
        try:
            from auth.authorization_modal import UsersMaster
            from notifications.notifications_service import NotificationsService
            sender = UsersMaster().find_by_id(connection, from_id) or {}
            title = sender.get("name") or "New message"
            body = msg["text"] if len(msg["text"]) <= PUSH_PREVIEW_CHARS else msg["text"][:PUSH_PREVIEW_CHARS - 1] + "…"
            NotificationsService().send_push(
                connection, [to_id], title, body,
                {"type": "new_message", "booking_id": booking_id, "message_id": msg["message_id"]},
                record_in_app=False,
            )
        except Exception as e:
            print(f"[Chat] Push notification failed (non-fatal): {e}")


def _check_rate_limit(user_id, booking_id):
    from utilities.redis_connection import get_redis
    r = get_redis()
    key = f"chat_rate:{user_id}:{booking_id}"
    count = r.incr(key)
    if r.ttl(key) < 0:
        r.expire(key, 60)
    if count > CHAT_RATE_PER_MIN:
        raise ValueError("You're sending messages too fast. Please wait a moment.")
