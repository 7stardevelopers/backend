from sqlalchemy import func, text
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class ReviewsMaster:
    @property
    def r(self):
        return get_table("reviews")

    def create(self, conn, data: dict) -> dict:
        data["review_id"] = new_uuid()
        data["is_deleted"] = False
        data["created_at"] = now_utc()
        conn.execute(self.r.insert().values(**data))
        return data

    def find_by_booking(self, conn, booking_id: str):
        sel = self.r.select().where(self.r.c.booking_id == booking_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def list_for_service(self, conn, service_id: str, page: int = 1):
        sel = self.r.select().where(
            self.r.c.service_id == service_id
        ).where(self.r.c.is_deleted == False).order_by(
            self.r.c.created_at.desc()
        ).limit(20).offset((page-1)*20)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_for_provider(self, conn, provider_id: str, page: int = 1):
        sel = self.r.select().where(
            self.r.c.provider_id == provider_id
        ).where(self.r.c.is_deleted == False).order_by(
            self.r.c.created_at.desc()
        ).limit(20).offset((page-1)*20)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_all(self, conn, page: int = 1):
        sel = self.r.select().where(self.r.c.is_deleted == False).order_by(
            self.r.c.created_at.desc()
        ).limit(20).offset((page-1)*20)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def soft_delete(self, conn, review_id: str):
        conn.execute(self.r.update().where(self.r.c.review_id == review_id).values(is_deleted=True))

    def get_avg_rating(self, conn, provider_id: str) -> tuple:
        result = conn.execute(
            text("SELECT AVG(rating), COUNT(*) FROM reviews WHERE provider_id=:pid AND is_deleted=false"),
            {"pid": provider_id}
        ).fetchone()
        avg = float(result[0] or 0)
        count = int(result[1] or 0)
        return round(avg, 2), count
