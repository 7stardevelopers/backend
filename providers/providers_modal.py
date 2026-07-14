from sqlalchemy import text
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class ProvidersMaster:
    @property
    def p(self):
        return get_table("providers")

    @property
    def ps(self):
        return get_table("provider_services")

    @property
    def pl(self):
        return get_table("provider_locations")

    @property
    def u(self):
        return get_table("users")

    def find_by_user_id(self, conn, user_id: str):
        sel = self.p.select().where(self.p.c.user_id == user_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def find_by_id(self, conn, provider_id: str):
        sel = self.p.select().where(self.p.c.provider_id == provider_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def create(self, conn, user_id: str):
        provider_id = new_uuid()
        conn.execute(self.p.insert().values(
            provider_id=provider_id,
            user_id=user_id,
            status="PENDING",
            is_available=False,
            avg_rating=0,
            total_reviews=0,
            acceptance_rate=1.0,
            avg_response_secs=30,
            wallet_balance=0,
            years_experience=0,
            created_at=now_utc(),
        ))
        return self.find_by_id(conn, provider_id)

    def update(self, conn, provider_id: str, fields: dict):
        conn.execute(self.p.update().where(self.p.c.provider_id == provider_id).values(**fields))

    def upsert_location(self, conn, provider_id: str, lat: float, lng: float):
        conn.execute(text("""
            INSERT INTO provider_locations (provider_id, lat, lng, updated_at)
            VALUES (:pid, :lat, :lng, NOW())
            ON DUPLICATE KEY UPDATE lat=:lat, lng=:lng, updated_at=NOW()
        """), {"pid": provider_id, "lat": lat, "lng": lng})

    def get_location(self, conn, provider_id: str):
        sel = self.pl.select().where(self.pl.c.provider_id == provider_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def list_all(self, conn, status=None, page=1, per_page=20):
        sel = self.p.select().order_by(self.p.c.created_at.desc()).limit(per_page).offset((page-1)*per_page)
        if status:
            sel = sel.where(self.p.c.status == status)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_services(self, conn, provider_id: str):
        sel = self.ps.select().where(self.ps.c.provider_id == provider_id)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def set_services(self, conn, provider_id: str, service_ids: list):
        conn.execute(self.ps.delete().where(self.ps.c.provider_id == provider_id))
        if service_ids:
            conn.execute(self.ps.insert(), [
                {"provider_id": provider_id, "service_id": sid} for sid in service_ids
            ])

    def get_available_for_service(self, conn, service_id: str):
        result = conn.execute(text("""
            SELECT p.*, pl.lat as last_lat, pl.lng as last_lng
            FROM providers p
            JOIN provider_services ps ON p.provider_id = ps.provider_id
            LEFT JOIN provider_locations pl ON p.provider_id = pl.provider_id
            WHERE p.status = 'APPROVED'
              AND p.is_available = TRUE
              AND ps.service_id = :sid
        """), {"sid": service_id})
        return [dict(r._mapping) for r in result.fetchall()]

    def update_rating(self, conn, provider_id: str, new_rating: float, total_reviews: int):
        conn.execute(
            self.p.update().where(self.p.c.provider_id == provider_id)
            .values(avg_rating=new_rating, total_reviews=total_reviews)
        )

    def update_wallet(self, conn, provider_id: str, amount: int, operation: str = "credit"):
        if operation == "credit":
            conn.execute(text(
                "UPDATE providers SET wallet_balance = wallet_balance + :amt WHERE provider_id = :pid"
            ), {"amt": amount, "pid": provider_id})
        else:
            conn.execute(text(
                "UPDATE providers SET wallet_balance = wallet_balance - :amt WHERE provider_id = :pid"
            ), {"amt": amount, "pid": provider_id})
