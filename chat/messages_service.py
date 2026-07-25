import json
import os
import boto3

from chat.messages_modal import MessagesMaster
from chat.messages_validator import SendMessageSchema
from bookings.bookings_modal import BookingsMaster


def _provider_user_id(connection, booking_provider_id):
    """Resolve providers.provider_id → users.user_id."""
    if not booking_provider_id:
        return None
    from providers.providers_modal import ProvidersMaster
    prov = ProvidersMaster().find_by_id(connection, booking_provider_id)
    return str(prov["user_id"]) if prov else None


class MessagesService:
    def __init__(self):
        self.modal = MessagesMaster()
        self.booking_modal = BookingsMaster()

    def send_message(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        # booking_id can come from path param (id) or body
        booking_id = obj.get("id") or obj.get("booking_id")
        obj["booking_id"] = booking_id
        data = SendMessageSchema(**{k: v for k, v in obj.items() if not k.startswith("_") and k != "id"})

        booking = self.booking_modal.read_one(connection, data.booking_id)
        customer_id      = str(booking["customer_id"])
        prov_user_id     = _provider_user_id(connection, booking.get("provider_id"))

        if str(user_id) == customer_id:
            if not prov_user_id:
                raise ValueError("No provider assigned to this booking yet")
            to_id = prov_user_id
        elif prov_user_id and str(user_id) == prov_user_id:
            to_id = customer_id
        else:
            raise PermissionError("You are not a participant of this booking")

        msg = self.modal.send(connection, user_id, to_id, data.booking_id, data.text, data.message_type)
        self._push_via_websocket(connection, to_id, msg)
        return "created", msg

    def list_messages(self, obj, connection):
        user_id = obj.pop("_user_id")
        role    = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")

        booking = self.booking_modal.read_one(connection, booking_id)
        customer_id  = str(booking["customer_id"])
        prov_user_id = _provider_user_id(connection, booking.get("provider_id"))

        is_customer = str(user_id) == customer_id
        is_provider = prov_user_id and str(user_id) == prov_user_id
        if not is_customer and not is_provider and role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Access denied")

        self.modal.mark_seen(connection, booking_id, user_id)
        messages = self.modal.list_for_booking(connection, booking_id)
        return "success", messages

    def _push_via_websocket(self, connection, to_user_id: str, msg: dict):
        from utilities.db_connection import metadata
        ws = metadata.tables.get("ws_connections")
        if not ws:
            return
        endpoint = os.environ.get("WEBSOCKET_ENDPOINT_URL", "")
        if not endpoint:
            return
        try:
            rows = connection.execute(ws.select().where(ws.c.user_id == to_user_id)).fetchall()
            client = boto3.client(
                "apigatewaymanagementapi",
                endpoint_url=endpoint,
                region_name=os.environ.get("AWS_REGION_NAME", "ap-south-1"),
            )
            for row in rows:
                try:
                    client.post_to_connection(
                        ConnectionId=row["connection_id"],
                        Data=json.dumps({"message_type": "chat", **msg}, default=str).encode(),
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[Chat] WS delivery failed (non-fatal): {e}")
