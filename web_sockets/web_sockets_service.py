import json

from web_sockets.web_sockets_modal import WebSocketsMaster


class WebSocketsService:
    def __init__(self):
        self.modal = WebSocketsMaster()

    def on_connect(self, connection_id: str, event: dict, conn):
        query = event.get("queryStringParameters") or {}
        token = query.get("token", "")
        booking_id = query.get("bookingId")
        user_id, role = _extract_user_from_token(token)
        if not user_id:
            return "error", "Unauthorized"
        if booking_id and not _is_booking_participant(conn, booking_id, user_id, role):
            booking_id = None
        self.modal.connect(conn, connection_id, user_id, booking_id, role)
        return "success", "Connected"

    def on_disconnect(self, connection_id: str, event: dict, conn):
        self.modal.disconnect(conn, connection_id)
        return "success", "Disconnected"

    def on_message(self, connection_id: str, event: dict, conn):
        body = _parse_body(event)
        ws_record = self.modal.get_connection(conn, connection_id)
        if not ws_record:
            return "error", "Connection not found"
        from chat.messages_service import MessagesService
        svc = MessagesService()
        result = svc.send_message({
            "_user_id": ws_record["user_id"],
            "_role": ws_record.get("role"),
            "booking_id": body.get("booking_id") or ws_record.get("booking_id"),
            "text": body.get("text", ""),
            "message_type": body.get("message_type", "text"),
        }, conn)
        return result

    def on_location(self, connection_id: str, event: dict, conn):
        body = _parse_body(event)
        ws_record = self.modal.get_connection(conn, connection_id)
        if not ws_record:
            return "error", "Connection not found"
        lat = body.get("lat")
        lng = body.get("lng")
        if lat is None or lng is None:
            return "error", "lat/lng required"
        from providers.providers_modal import ProvidersMaster
        p_modal = ProvidersMaster()
        provider = p_modal.find_by_user_id(conn, ws_record["user_id"])
        if provider:
            p_modal.upsert_location(conn, provider["provider_id"], float(lat), float(lng))
            booking_id = body.get("booking_id") or ws_record.get("booking_id")
            if booking_id and _provider_assigned(conn, booking_id, provider["provider_id"]):
                _broadcast_location_to_customer(conn, booking_id, float(lat), float(lng))
        return "success", "Location updated"

    def on_mark_delivered(self, connection_id: str, event: dict, conn):
        body = _parse_body(event)
        ws_record = self.modal.get_connection(conn, connection_id)
        if not ws_record:
            return "error", "Connection not found"
        booking_id = body.get("booking_id")
        if booking_id and _is_booking_participant(conn, booking_id, ws_record["user_id"], ws_record.get("role")):
            from chat.messages_modal import MessagesMaster
            MessagesMaster().mark_seen(conn, booking_id, ws_record["user_id"])
        return "success", "Delivered"

    def on_join_booking(self, connection_id: str, event: dict, conn):
        body = _parse_body(event)
        booking_id = body.get("booking_id") or body.get("bookingId")
        if not booking_id:
            return "error", "booking_id required"
        ws_record = self.modal.get_connection(conn, connection_id)
        if not ws_record:
            return "error", "Connection not found"
        if not _is_booking_participant(conn, booking_id, ws_record["user_id"], ws_record.get("role")):
            return "error", "Access denied"
        self.modal.set_booking(conn, connection_id, booking_id)
        return "success", "Joined booking"

    def on_default(self, connection_id: str, event: dict, conn):
        return "success", "OK"


def _extract_user_from_token(token: str):
    if not token:
        return None, None
    from utilities.auth_tokens import decode_access_token
    try:
        payload = decode_access_token(token)
        return payload.get("user_id"), payload.get("role")
    except PermissionError:
        return None, None


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}


def _is_booking_participant(conn, booking_id, user_id, role=None) -> bool:
    if role in ("ADMIN", "SUPPORT"):
        return True
    from sqlalchemy import text
    row = conn.execute(text("""
        SELECT 1 FROM bookings b
        LEFT JOIN providers p ON p.provider_id = b.provider_id
        WHERE b.booking_id = :bid AND (b.customer_id = :uid OR p.user_id = :uid)
    """), {"bid": booking_id, "uid": user_id}).fetchone()
    return row is not None


def _provider_assigned(conn, booking_id, provider_id) -> bool:
    from utilities.db_connection import get_table
    b = get_table("bookings")
    row = conn.execute(
        b.select().where(b.c.booking_id == booking_id).where(b.c.provider_id == provider_id)
        .where(b.c.status.in_(["ACCEPTED", "EN_ROUTE", "IN_PROGRESS"]))
    ).fetchone()
    return row is not None


def _broadcast_location_to_customer(conn, booking_id: str, lat, lng, updated_at=None):
    from utilities.db_connection import get_table
    from utilities.ws_push import push_to_user
    from utilities.common_table_elements import now_utc
    try:
        bookings_t = get_table("bookings")
        booking = conn.execute(bookings_t.select().where(bookings_t.c.booking_id == booking_id)).fetchone()
        if not booking:
            return
        push_to_user(conn, booking.customer_id, {
            "message_type": "location_update", "lat": lat, "lng": lng, "booking_id": booking_id,
            "updated_at": (updated_at or now_utc()).isoformat(),
        })
    except Exception as e:
        print(f"[WS] Broadcast location failed (non-fatal): {e}")
