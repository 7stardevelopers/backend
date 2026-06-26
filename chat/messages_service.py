import json
import os
import boto3

from chat.messages_modal import MessagesMaster
from chat.messages_validator import SendMessageSchema
from bookings.bookings_modal import BookingsMaster


class MessagesService:
    def __init__(self):
        self.modal = MessagesMaster()
        self.booking_modal = BookingsMaster()

    def send_message(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        data = SendMessageSchema(**obj)

        booking = self.booking_modal.read_one(connection, data.booking_id)
        customer_id = str(booking["customer_id"])
        provider_id = str(booking.get("provider_id", ""))

        if str(user_id) == customer_id:
            to_id = provider_id
        elif str(user_id) == provider_id:
            to_id = customer_id
        else:
            raise PermissionError("You are not a participant of this booking")

        msg = self.modal.send(connection, user_id, to_id, data.booking_id, data.text, data.message_type)
        self._push_via_websocket(connection, to_id, msg)
        return "created", msg

    def list_messages(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("booking_id")
        booking = self.booking_modal.read_one(connection, booking_id)
        if str(booking["customer_id"]) != str(user_id) and str(booking.get("provider_id", "")) != str(user_id):
            if role not in ("ADMIN", "SUPPORT"):
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
            sel = ws.select().where(ws.c.user_id == to_user_id)
            rows = connection.execute(sel).fetchall()
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
