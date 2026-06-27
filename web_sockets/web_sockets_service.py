import json
import os
import jwt
import boto3

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
            if booking_id:
                _broadcast_location_to_customer(conn, booking_id, lat, lng)
        return "success", "Location updated"

    def on_mark_delivered(self, connection_id: str, event: dict, conn):
        body = _parse_body(event)
        ws_record = self.modal.get_connection(conn, connection_id)
        if not ws_record:
            return "error", "Connection not found"
        booking_id = body.get("booking_id")
        if booking_id:
            from chat.messages_modal import MessagesMaster
            MessagesMaster().mark_seen(conn, booking_id, ws_record["user_id"])
        return "success", "Delivered"

    def on_default(self, connection_id: str, event: dict, conn):
        return "success", "OK"


def _extract_user_from_token(token: str):
    if not token:
        return None, None
    try:
        secret = os.environ.get("JWT_SECRET", "")
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload.get("user_id"), payload.get("role")
    except Exception:
        return None, None


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}


def _broadcast_location_to_customer(conn, booking_id: str, lat, lng):
    from utilities.db_connection import get_table
    try:
        ws_t = get_table("ws_connections")
        bookings_t = get_table("bookings")
    except KeyError:
        return
    endpoint = os.environ.get("WEBSOCKET_ENDPOINT_URL", "")
    if not endpoint:
        return
    try:
        booking = conn.execute(bookings_t.select().where(bookings_t.c.booking_id == booking_id)).fetchone()
        if not booking:
            return
        customer_id = booking["customer_id"]
        ws_rows = conn.execute(ws_t.select().where(ws_t.c.user_id == customer_id)).fetchall()
        client = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=endpoint,
            region_name=os.environ.get("AWS_REGION_NAME", "ap-south-1"),
        )
        payload = json.dumps({"message_type": "location_update", "lat": lat, "lng": lng, "booking_id": booking_id}, default=str).encode()
        for row in ws_rows:
            try:
                client.post_to_connection(ConnectionId=row["connection_id"], Data=payload)
            except Exception:
                pass
    except Exception as e:
        print(f"[WS] Broadcast location failed (non-fatal): {e}")
