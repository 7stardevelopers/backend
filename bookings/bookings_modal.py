import random
import string
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class BookingsMaster:
    @property
    def t(self):
        return get_table("bookings")

    @property
    def items(self):
        return get_table("booking_items")

    def create(self, conn, obj: dict) -> dict:
        obj["booking_id"] = new_uuid()
        obj["door_otp"] = "".join(random.choices(string.digits, k=4))
        obj["created_at"] = now_utc()
        obj["updated_at"] = now_utc()
        conn.execute(self.t.insert().values(**obj))
        return obj

    def read(self, conn, filters: dict = None, limit: int = 50, offset: int = 0) -> list:
        sel = self.t.select().order_by(self.t.c.created_at.desc()).limit(limit).offset(offset)
        if filters:
            for k, v in filters.items():
                if hasattr(self.t.c, k):
                    sel = sel.where(getattr(self.t.c, k) == v)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def read_one(self, conn, booking_id: str) -> dict:
        sel = self.t.select().where(self.t.c.booking_id == booking_id)
        row = conn.execute(sel).fetchone()
        if not row:
            raise ValueError(f"Booking {booking_id} not found")
        return dict(row._mapping)

    def update_status(self, conn, booking_id: str, status: str) -> dict:
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(status=status, updated_at=now_utc())
        )
        return self.read_one(conn, booking_id)

    def assign_provider(self, conn, booking_id: str, provider_id: str):
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(provider_id=provider_id, status="ACCEPTED", updated_at=now_utc())
        )

    def verify_door_otp(self, conn, booking_id: str, otp: str) -> bool:
        booking = self.read_one(conn, booking_id)
        if booking.get("door_otp") != otp:
            return False
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(door_otp_verified=True, updated_at=now_utc())
        )
        return True

    def create_items(self, conn, booking_id: str, items: list):
        for item in items:
            item["booking_id"] = booking_id
            conn.execute(self.items.insert().values(**item))

    def get_items(self, conn, booking_id: str) -> list:
        sel = self.items.select().where(self.items.c.booking_id == booking_id)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def update_payment(self, conn, booking_id: str, payment_id: str, payment_status: str):
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(payment_id=payment_id, payment_status=payment_status, updated_at=now_utc())
        )

    def update_proof_photos(self, conn, booking_id: str, photo_urls: list):
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(proof_photos=photo_urls, updated_at=now_utc())
        )
