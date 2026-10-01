from sqlalchemy import text
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class NotificationsMaster:
    @property
    def tc(self):
        return get_table("token_connections")

    def register_token(self, conn, user_id: str, token_id: str, device_type: str = None):
        sel = self.tc.select().where(self.tc.c.token_id == token_id)
        existing = conn.execute(sel).fetchone()
        if existing and str(existing.user_id) != str(user_id):
            # Same device, new account — move the token so the previous user's
            # notifications stop arriving on this phone.
            conn.execute(self.tc.update().where(self.tc.c.token_id == token_id)
                         .values(user_id=user_id, device_type=device_type))
        elif not existing:
            conn.execute(self.tc.insert().values(
                user_id=user_id,
                token_id=token_id,
                device_type=device_type,
                created_at=now_utc(),
            ))

    def unregister_token(self, conn, user_id: str, token_id: str):
        conn.execute(self.tc.delete().where(self.tc.c.token_id == token_id).where(self.tc.c.user_id == user_id))

    def get_tokens_for_users(self, conn, user_ids: list) -> list:
        user_ids_str = [str(u) for u in user_ids]
        sel = self.tc.select().where(self.tc.c.user_id.in_(user_ids_str))
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_all_tokens_for_role(self, conn, role: str) -> list:
        if role == "ALL":
            result = conn.execute(text(
                "SELECT tc.* FROM token_connections tc JOIN users u ON tc.user_id = u.user_id WHERE u.status = 'ACTIVE'"
            ))
            return [dict(r._mapping) for r in result.fetchall()]
        result = conn.execute(text(
            "SELECT tc.* FROM token_connections tc JOIN users u ON tc.user_id = u.user_id WHERE u.role = :role"
        ), {"role": role})
        return [dict(r._mapping) for r in result.fetchall()]
