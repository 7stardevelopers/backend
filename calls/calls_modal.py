from sqlalchemy import text

from utilities.db_connection import get_table, get_engine
from utilities.common_table_elements import new_uuid, now_utc


class CallsMaster:
    @property
    def t(self):
        return get_table("call_logs")

    def _known(self, fields: dict) -> dict:
        """Drop keys for columns that don't exist yet (migration 002 not run)."""
        cols = set(self.t.c.keys())
        return {k: v for k, v in fields.items() if k in cols}

    def create(self, conn, data: dict) -> dict:
        data["call_id"] = new_uuid()
        data["created_at"] = now_utc()
        data["updated_at"] = now_utc()
        conn.execute(self.t.insert().values(**self._known(data)))
        return data

    def create_committed(self, data: dict) -> dict:
        """Insert in its own transaction — used for FAILED attempts, which are
        recorded right before the request raises (and its transaction rolls back)."""
        with get_engine().begin() as own:
            return self.create(own, data)

    def find_by_id(self, conn, call_id: str):
        row = conn.execute(self.t.select().where(self.t.c.call_id == call_id)).fetchone()
        return dict(row._mapping) if row else None

    def update_by_sid(self, conn, exotel_call_sid: str, fields: dict) -> bool:
        fields = self._known({**fields, "updated_at": now_utc()})
        result = conn.execute(
            self.t.update().where(self.t.c.exotel_call_sid == exotel_call_sid).values(**fields)
        )
        return result.rowcount > 0

    def list_admin(self, conn, booking_id=None, status=None, user_id=None, page=1, per_page=20) -> dict:
        where, params = [], {"lim": per_page, "off": (page - 1) * per_page}
        if booking_id:
            where.append("c.booking_id = :bid")
            params["bid"] = booking_id
        if status:
            where.append("c.status = :status")
            params["status"] = status.upper()
        if user_id:
            where.append("(c.initiated_by = :uid OR b.customer_id = :uid OR p.user_id = :uid)")
            params["uid"] = user_id
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        from_sql = """
            FROM call_logs c
            LEFT JOIN bookings b ON b.booking_id = c.booking_id
            LEFT JOIN providers p ON p.provider_id = b.provider_id
            LEFT JOIN users caller ON caller.user_id = c.initiated_by
        """
        rows = conn.execute(text(f"""
            SELECT c.*, caller.name AS initiated_by_name, caller.role AS initiated_by_role
            {from_sql} {where_sql}
            ORDER BY c.created_at DESC
            LIMIT :lim OFFSET :off
        """), params).mappings().fetchall()
        total = conn.execute(text(f"SELECT COUNT(*) {from_sql} {where_sql}"), params).scalar() or 0
        return {"items": [dict(r) for r in rows], "total": int(total)}
