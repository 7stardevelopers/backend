import os
import requests

from notifications.notifications_modal import NotificationsMaster
from utilities.db_connection import metadata

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_BATCH_LIMIT = 100


class NotificationsService:
    def __init__(self):
        self.modal = NotificationsMaster()

    def register_token(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        token_id = obj.get("token_id") or obj.get("expo_push_token")
        device_type = obj.get("device_type")
        if not token_id:
            raise ValueError("token_id required")
        self.modal.register_token(connection, user_id, token_id, device_type)
        return "success", {"message": "Token registered"}

    def unregister_token(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        token_id = obj.get("token_id")
        if not token_id:
            raise ValueError("token_id required")
        self.modal.unregister_token(connection, token_id)
        return "success", {"message": "Token removed"}

    def list_in_app(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        page = int(obj.get("page", 1))
        try:
            t = metadata.tables["in_app_notifications"]
            sel = t.select().where(t.c.user_id == user_id).order_by(
                t.c.created_at.desc()
            ).limit(30).offset((page-1)*30)
            rows = connection.execute(sel).fetchall()
            return "success", [dict(r._mapping) for r in rows]
        except Exception as e:
            return "success", []

    def mark_read(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        notif_id = obj.get("id") or obj.get("notification_id")
        try:
            t = metadata.tables["in_app_notifications"]
            connection.execute(
                t.update().where(t.c.notification_id == notif_id).where(t.c.user_id == user_id)
                .values(read_ind=True)
            )
        except Exception:
            pass
        return "success", {"message": "Marked as read"}

    def admin_announce(self, obj, connection):
        role = obj.pop("_role", None)
        sender_id = obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        target_role = obj.get("target_role", "CUSTOMER")
        title = obj.get("title", "")
        body = obj.get("body", "")
        tokens = self.modal.get_all_tokens_for_role(connection, target_role)
        token_ids = [t["token_id"] for t in tokens if t.get("token_id", "").startswith("ExponentPushToken[")]
        self._batch_push(token_ids, title, body, {"type": "announcement"})
        # Record one history row for the admin UI's announcement list — attributed
        # to the sending admin, not fanned out per recipient (which could be
        # thousands of rows for a broadcast to "ALL").
        if sender_id:
            from notifications.in_app_notifications.in_app_notifications_service import InAppNotificationsService
            InAppNotificationsService().record_and_push(
                connection, [sender_id], title, body, "announcement", {"target_role": target_role}
            )
        return "success", {"message": f"Announcement sent to {len(token_ids)} devices"}

    def send_push(self, connection, user_ids: list, title: str, body: str, data: dict = None):
        try:
            token_rows = self.modal.get_tokens_for_users(connection, user_ids)
            tokens = [r["token_id"] for r in token_rows if r.get("token_id", "").startswith("ExponentPushToken[")]
            if not tokens:
                return
            try:
                from notifications.in_app_notifications.in_app_notifications_service import InAppNotificationsService
                InAppNotificationsService().record_and_push(
                    connection, user_ids, title, body,
                    (data or {}).get("type", "system"), data or {}
                )
            except Exception as e:
                print(f"[Notify] In-app push failed (non-fatal): {e}")
            self._batch_push(tokens, title, body, data or {})
        except Exception as e:
            print(f"[Notify] Push failed (non-fatal): {e}")

    def _batch_push(self, tokens: list, title: str, body: str, data: dict):
        messages = [
            {"to": t, "sound": "default", "title": title, "body": body, "data": data}
            for t in tokens
        ]
        for i in range(0, len(messages), EXPO_BATCH_LIMIT):
            batch = messages[i:i+EXPO_BATCH_LIMIT]
            try:
                requests.post(
                    EXPO_PUSH_URL,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    json=batch,
                    timeout=10,
                )
            except Exception as e:
                print(f"[Notify] Batch send failed (non-fatal): {e}")
