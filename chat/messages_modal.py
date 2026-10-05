from sqlalchemy import and_, or_, func
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc

MAX_PAGE = 100


class MessagesMaster:
    @property
    def m(self):
        return get_table("chat_messages")

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
        msg["seen_at"] = None
        msg["delivered_at"] = None
        return msg

    def list_for_booking(self, conn, booking_id: str, limit: int = 50, before_id: str = None, since=None) -> list:
        """Messages in chronological order.
        - before_id: page backwards — up to `limit` messages older than that message
        - since:     cheap poll — messages created at/after this naive-UTC datetime
                     (inclusive, because created_at has second precision; clients dedupe by id)
        """
        limit = max(1, min(int(limit or 50), MAX_PAGE))
        m = self.m
        sel = m.select().where(m.c.booking_id == booking_id)
        if since is not None:
            sel = sel.where(m.c.created_at >= since).order_by(m.c.created_at.asc(), m.c.message_id.asc()).limit(limit)
            return [dict(r._mapping) for r in conn.execute(sel).fetchall()]
        if before_id:
            anchor = conn.execute(
                m.select().where(m.c.message_id == before_id).where(m.c.booking_id == booking_id)
            ).fetchone()
            if anchor is not None:
                sel = sel.where(or_(
                    m.c.created_at < anchor.created_at,
                    and_(m.c.created_at == anchor.created_at, m.c.message_id < anchor.message_id),
                ))
        sel = sel.order_by(m.c.created_at.desc(), m.c.message_id.desc()).limit(limit)
        rows = conn.execute(sel).fetchall()
        return list(reversed([dict(r._mapping) for r in rows]))

    def mark_delivered(self, conn, message_id: str):
        ts = now_utc()
        conn.execute(
            self.m.update().where(self.m.c.message_id == message_id)
            .where(self.m.c.delivered_at.is_(None)).values(delivered_at=ts)
        )
        return ts

    def mark_seen(self, conn, booking_id: str, user_id: str):
        """Mark every unseen message *to* user_id in this booking as seen.
        Returns (count, seen_at)."""
        ts = now_utc()
        result = conn.execute(
            self.m.update()
            .where(self.m.c.booking_id == booking_id)
            .where(self.m.c.to_id == user_id)
            .where(self.m.c.seen_at.is_(None))
            .values(seen_at=ts, delivered_at=func.coalesce(self.m.c.delivered_at, ts))
        )
        return result.rowcount, ts
