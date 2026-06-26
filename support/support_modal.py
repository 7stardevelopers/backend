from utilities.db_connection import metadata
from utilities.common_table_elements import new_uuid, now_utc


class SupportMaster:
    def __init__(self):
        self.t = metadata.tables["support_tickets"]
        self.m = metadata.tables["ticket_messages"]

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
        sel = self.t.select().order_by(self.t.c.created_at.desc()).limit(20).offset((page-1)*20)
        if status:
            sel = sel.where(self.t.c.status == status)
        if priority:
            sel = sel.where(self.t.c.priority == priority)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_one(self, conn, ticket_id: str):
        sel = self.t.select().where(self.t.c.ticket_id == ticket_id)
        row = conn.execute(sel).fetchone()
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
        sel = self.m.select().where(self.m.c.ticket_id == ticket_id).order_by(self.m.c.created_at)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]
