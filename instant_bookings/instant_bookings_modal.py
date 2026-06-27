from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc
from datetime import datetime, timezone


class InstantBookingsMaster:
    @property
    def t(self):
        return get_table("instant_bookings")

    def create(self, conn, booking_id: str, surge_applied: bool = False, surge_pct: int = 0) -> dict:
        record = {
            "instant_id": new_uuid(),
            "booking_id": booking_id,
            "requested_at": now_utc(),
            "sla_minutes": 60,
            "surge_applied": surge_applied,
            "surge_pct": surge_pct,
            "status": "DISPATCHING",
        }
        conn.execute(self.t.insert().values(**record))
        return record

    def get_by_booking(self, conn, booking_id: str):
        sel = self.t.select().where(self.t.c.booking_id == booking_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def update(self, conn, instant_id: str, fields: dict):
        conn.execute(self.t.update().where(self.t.c.instant_id == instant_id).values(**fields))
