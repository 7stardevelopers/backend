from utilities.db_connection import get_table
from utilities.common_table_elements import now_utc


class WebSocketsMaster:
    @property
    def ws(self):
        return get_table("ws_connections")

    def connect(self, conn, connection_id: str, user_id: str, booking_id: str = None, role: str = None):
        ins = self.ws.insert().values(
            connection_id=connection_id,
            user_id=user_id,
            booking_id=booking_id,
            role=role,
            connected_at=now_utc(),
        )
        conn.execute(ins)

    def disconnect(self, conn, connection_id: str):
        conn.execute(self.ws.delete().where(self.ws.c.connection_id == connection_id))

    def get_connections_for_user(self, conn, user_id: str) -> list:
        sel = self.ws.select().where(self.ws.c.user_id == user_id)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_connection(self, conn, connection_id: str):
        sel = self.ws.select().where(self.ws.c.connection_id == connection_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None
