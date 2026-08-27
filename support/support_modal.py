from sqlalchemy import text
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class SupportMaster:
    @property
    def t(self):
        return get_table("support_tickets")

    @property
    def m(self):
        return get_table("ticket_messages")

    def create_ticket(self, conn, data: dict) -> dict:
        data["ticket_id"] = new_uuid()
        data["status"] = "OPEN"
        data["created_at"] = now_utc()
        data["updated_at"] = now_utc()
        conn.execute(self.t.insert().values(**data))
        return data

    def list_by_user(self, conn, user_id: str, page: int = 1):
        sel = self.t.select().where(self.t.c.user_id == user_id).order_by(
            self.t.c.created_at.desc()
        ).limit(20).offset((page-1)*20)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_all(self, conn, status=None, priority=None, page=1):
        sql = """
            SELECT t.*, u.name AS user_name, u.phone AS user_phone
            FROM support_tickets t
            JOIN users u ON u.user_id = t.user_id
            WHERE 1=1
        """
        params = {"limit": 20, "offset": (page - 1) * 20}
        if status:
            sql += " AND t.status = :status"
            params["status"] = status
        if priority:
            sql += " AND t.priority = :priority"
            params["priority"] = priority
        sql += " ORDER BY t.created_at DESC LIMIT :limit OFFSET :offset"
        rows = conn.execute(text(sql), params).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_one(self, conn, ticket_id: str):
        sql = text("""
            SELECT t.*, u.name AS user_name, u.phone AS user_phone
            FROM support_tickets t
            JOIN users u ON u.user_id = t.user_id
            WHERE t.ticket_id = :tid
        """)
        row = conn.execute(sql, {"tid": ticket_id}).fetchone()
        if not row:
            raise ValueError("Ticket not found")
        return dict(row._mapping)

    def update(self, conn, ticket_id: str, fields: dict):
        fields["updated_at"] = now_utc()
        conn.execute(self.t.update().where(self.t.c.ticket_id == ticket_id).values(**fields))

    def add_message(self, conn, ticket_id: str, sender_id: str, content: str, is_internal: bool = False) -> dict:
        msg = {
            "message_id": new_uuid(),
            "ticket_id": ticket_id,
            "sender_id": sender_id,
            "content": content,
            "is_internal": is_internal,
            "created_at": now_utc(),
        }
        conn.execute(self.m.insert().values(**msg))
        return msg

    def get_messages(self, conn, ticket_id: str):
        sql = text("""
            SELECT m.*, u.name AS sender_name, u.role AS sender_role
            FROM ticket_messages m
            JOIN users u ON u.user_id = m.sender_id
            WHERE m.ticket_id = :tid
            ORDER BY m.created_at
        """)
        rows = conn.execute(sql, {"tid": ticket_id}).fetchall()
        return [dict(r._mapping) for r in rows]
