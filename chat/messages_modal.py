from sqlalchemy import text
from utilities.db_connection import metadata
from utilities.common_table_elements import new_uuid, now_utc


class MessagesMaster:
    def __init__(self):
        self.m = metadata.tables["chat_messages"]

    def send(self, conn, from_id: str, to_id: str, booking_id: str, text: str, message_type: str = "text") -> dict:
        msg = {
            "message_id": new_uuid(),
            "booking_id": booking_id,
            "from_id": from_id,
            "to_id": to_id,
            "text": text,
            "message_type": message_type,
            "created_at": now_utc(),
        }
        conn.execute(self.m.insert().values(**msg))
        return msg

    def list_for_booking(self, conn, booking_id: str, limit: int = 50, before_id: str = None) -> list:
        sel = self.m.select().where(self.m.c.booking_id == booking_id).order_by(
            self.m.c.created_at.desc()
        ).limit(limit)
        rows = conn.execute(sel).fetchall()
        return list(reversed([dict(r._mapping) for r in rows]))

    def mark_seen(self, conn, booking_id: str, user_id: str):
        conn.execute(text(
            "UPDATE chat_messages SET seen_at = NOW() WHERE booking_id = :bid AND to_id = :uid AND seen_at IS NULL"
        ), {"bid": booking_id, "uid": user_id})
