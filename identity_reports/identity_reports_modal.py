from sqlalchemy import text
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class IdentityReportsMaster:
    @property
    def t(self):
        return get_table("identity_reports")

    def find_by_booking(self, conn, booking_id: str):
        row = conn.execute(self.t.select().where(self.t.c.booking_id == booking_id)).fetchone()
        return dict(row._mapping) if row else None

    def find_by_id(self, conn, report_id: str):
        row = conn.execute(self.t.select().where(self.t.c.report_id == report_id)).fetchone()
        return dict(row._mapping) if row else None

    def create(self, conn, booking: dict, note=None, ticket_id=None) -> dict:
        obj = {
            "report_id": new_uuid(),
            "booking_id": booking["booking_id"],
            "provider_id": booking.get("provider_id"),
            "customer_id": booking["customer_id"],
            "ticket_id": ticket_id,
            "customer_note": note,
            "status": "OPEN",
            "created_at": now_utc(),
        }
        conn.execute(self.t.insert().values(**obj))
        return obj

    def update(self, conn, report_id: str, fields: dict):
        conn.execute(self.t.update().where(self.t.c.report_id == report_id).values(**fields))

    def list_detailed(self, conn, status=None, page=1, per_page=20):
        where = "WHERE r.status = :status" if status else ""
        params = {"lim": per_page, "off": (page - 1) * per_page}
        if status:
            params["status"] = status
        sql = f"""
            SELECT r.report_id, r.booking_id, r.provider_id, r.customer_id, r.ticket_id,
                   r.customer_note, r.status, r.admin_note, r.resolved_at, r.created_at,
                   b.status AS booking_status, b.scheduled_at, b.identity_confirmed_at,
                   s.name   AS service_name,
                   pu.name  AS provider_name, pu.phone AS provider_phone, pu.photo_url AS provider_photo,
                   pr.status AS provider_status,
                   cu.name  AS customer_name, cu.phone AS customer_phone,
                   ru.name  AS resolved_by_name,
                   (SELECT COUNT(*) FROM identity_reports r2
                    WHERE r2.provider_id = r.provider_id) AS provider_report_count
            FROM identity_reports r
            LEFT JOIN bookings  b  ON b.booking_id  = r.booking_id
            LEFT JOIN services  s  ON s.service_id  = b.service_id
            LEFT JOIN providers pr ON pr.provider_id = r.provider_id
            LEFT JOIN users     pu ON pu.user_id    = pr.user_id
            LEFT JOIN users     cu ON cu.user_id    = r.customer_id
            LEFT JOIN users     ru ON ru.user_id    = r.resolved_by
            {where}
            ORDER BY r.created_at DESC
            LIMIT :lim OFFSET :off
        """
        rows = conn.execute(text(sql), params).mappings().fetchall()
        total = int(conn.execute(
            text(f"SELECT COUNT(*) FROM identity_reports r {where}"), {"status": status} if status else {}
        ).scalar() or 0)
        open_count = int(conn.execute(
            text("SELECT COUNT(*) FROM identity_reports WHERE status = 'OPEN'")
        ).scalar() or 0)
        return {"items": [dict(r) for r in rows], "total": total, "open_count": open_count}
