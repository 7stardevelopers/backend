from datetime import datetime, timezone
from sqlalchemy import text
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class CouponsMaster:
    @property
    def c(self):
        return get_table("coupons")

    @property
    def cu(self):
        return get_table("coupon_uses")

    def find_by_code(self, conn, code: str):
        sel = self.c.select().where(self.c.c.code == code.upper()).where(self.c.c.is_active == True)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def list_active(self, conn):
        sel = self.c.select().where(self.c.c.is_active == True).where(
            self.c.c.expires_at > datetime.now(timezone.utc)
        ).order_by(self.c.c.created_at.desc())
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def user_already_used(self, conn, user_id: str, coupon_id: str) -> bool:
        sel = self.cu.select().where(
            self.cu.c.user_id == user_id
        ).where(self.cu.c.coupon_id == coupon_id)
        return conn.execute(sel).fetchone() is not None

    def record_use(self, conn, coupon_id: str, user_id: str, booking_id: str):
        conn.execute(self.cu.insert().values(
            coupon_id=coupon_id,
            user_id=user_id,
            booking_id=booking_id,
            created_at=now_utc(),
        ))
        conn.execute(text("UPDATE coupons SET used_count = used_count + 1 WHERE coupon_id = :cid"),
                     {"cid": coupon_id})

    def create(self, conn, data: dict):
        data["coupon_id"] = new_uuid()
        data["created_at"] = now_utc()
        data["used_count"] = 0
        data["is_active"] = True
        if data.get("code"):
            data["code"] = data["code"].upper()
        conn.execute(self.c.insert().values(**data))
        return data

    def delete(self, conn, coupon_id: str):
        conn.execute(self.c.update().where(self.c.c.coupon_id == coupon_id).values(is_active=False))
