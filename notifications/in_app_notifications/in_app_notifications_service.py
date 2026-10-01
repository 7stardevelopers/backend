from notifications.in_app_notifications.in_app_notifications_modal import InAppNotificationsMaster
from utilities.ws_push import push_to_connections

ALLOWLISTED_TYPES = {
    "booking_confirmed", "booking_update", "job_request", "payment",
    "provider_approved", "support_reply", "announcement", "system",
    "booking_cancelled", "new_message",
    "booking_accepted", "payment_confirmed", "job_available", "instant_job",
    "instant_booking_confirmed", "live_tracking_active", "navigate_now",
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
                push_to_connections(connection, connection_ids, {"message_type": "notification", **notif})
            except Exception as e:
                print(f"[InApp] record_and_push failed for user {uid} (non-fatal): {e}")
