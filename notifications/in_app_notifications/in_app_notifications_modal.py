from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class InAppNotificationsMaster:
    @property
    def t(self):
        return get_table("in_app_notifications")

    def create(self, conn, user_id: str, title: str, body: str, notif_type: str, data: dict) -> dict:
        notif = {
            "notification_id": new_uuid(),
            "user_id": user_id,
            "title": title,
            "body": body,
            "type": notif_type,
            "data": data,
            "read_ind": False,
            "created_at": now_utc(),
        }
        conn.execute(self.t.insert().values(**notif))
        return notif

    def get_connections_for_user(self, conn, user_id: str) -> list:
        ws = get_table("ws_connections")
        sel = ws.select().where(ws.c.user_id == user_id)
        rows = conn.execute(sel).fetchall()
        return [r["connection_id"] for r in rows]
