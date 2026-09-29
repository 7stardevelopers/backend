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
        sql = text("""
            SELECT p.*, u.name, u.photo_url
            FROM providers p
            JOIN users u ON u.user_id = p.user_id
            WHERE p.provider_id = :pid
        """)
        row = conn.execute(sql, {"pid": provider_id}).fetchone()
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

    def list_all_locations(self, conn):
        result = conn.execute(text("""
            SELECT p.provider_id, u.name, p.status, p.is_available,
                   p.last_lat, p.last_lng, p.last_seen_at
            FROM providers p
            JOIN users u ON u.user_id = p.user_id
        """))
        return [dict(r._mapping) for r in result.fetchall()]

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

    def get_earnings(self, conn, provider_id: str) -> dict:
        rows = conn.execute(text("""
            SELECT earning_id, booking_id, amount, type, created_at
            FROM provider_earnings
            WHERE provider_id = :pid
            ORDER BY created_at DESC
            LIMIT 100
        """), {"pid": provider_id}).mappings().fetchall()

        stats = dict(conn.execute(text("""
            SELECT
              COALESCE(SUM(CASE WHEN type != 'DEDUCTION' THEN amount ELSE 0 END), 0) AS total_earned,
              COALESCE(SUM(CASE WHEN type = 'DEDUCTION'  THEN amount ELSE 0 END), 0) AS total_deducted,
              COUNT(*) AS total_entries
            FROM provider_earnings
            WHERE provider_id = :pid
        """), {"pid": provider_id}).mappings().one())

        return {"items": [dict(r) for r in rows], "stats": stats}

    def list_all_detailed(self, conn, status=None, page=1, per_page=20):
        where  = "WHERE p.status = :status" if status else ""
        params = {"lim": per_page, "off": (page - 1) * per_page}
        if status:
            params["status"] = status
        sql = f"""
            SELECT p.provider_id, p.status, p.avg_rating, p.total_reviews,
                   p.wallet_balance, p.is_available, p.acceptance_rate,
                   p.years_experience, p.created_at,
                   u.user_id, u.name, u.phone, u.email, u.photo_url,
                   u.status AS user_status
            FROM providers p
            JOIN users u ON p.user_id = u.user_id
            {where}
            ORDER BY p.created_at DESC
            LIMIT :lim OFFSET :off
        """
        count_sql = f"SELECT COUNT(*) FROM providers p {where}"
        rows  = conn.execute(text(sql), params).mappings().fetchall()
        cp    = {"status": status} if status else {}
        total = int(conn.execute(text(count_sql), cp).scalar() or 0)
        return {"items": [dict(r) for r in rows], "total": total}
