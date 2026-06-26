import json
import os
import boto3

from notifications.in_app_notifications.in_app_notifications_modal import InAppNotificationsMaster

ALLOWLISTED_TYPES = {
    "booking_confirmed", "booking_update", "job_request", "payment",
    "provider_approved", "support_reply", "announcement", "system",
    "booking_cancelled", "new_message",
}


class InAppNotificationsService:
    def __init__(self):
        self.modal = InAppNotificationsMaster()

    def record_and_push(self, connection, user_ids: list, title: str, body: str, notif_type: str, data: dict):
        if notif_type not in ALLOWLISTED_TYPES:
            return
        for uid in user_ids:
            try:
                notif = self.modal.create(connection, uid, title, body, notif_type, data)
                connection_ids = self.modal.get_connections_for_user(connection, uid)
                for cid in connection_ids:
                    self._push_to_ws(cid, notif)
            except Exception as e:
                print(f"[InApp] record_and_push failed for user {uid} (non-fatal): {e}")

    def _push_to_ws(self, connection_id: str, payload: dict):
        endpoint = os.environ.get("WEBSOCKET_ENDPOINT_URL", "")
        if not endpoint:
            return
        try:
            client = boto3.client(
                "apigatewaymanagementapi",
                endpoint_url=endpoint,
                region_name=os.environ.get("AWS_REGION_NAME", "ap-south-1"),
            )
            client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps({"message_type": "notification", **payload}, default=str).encode(),
            )
        except Exception as e:
            print(f"[InApp] WS push failed for {connection_id} (non-fatal): {e}")
