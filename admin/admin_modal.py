from sqlalchemy import text, func
from utilities.db_connection import get_table
from utilities.common_table_elements import now_utc


class AdminMaster:
    @property
    def users(self):
        return get_table("users")

    @property
    def bookings(self):
        return get_table("bookings")

    @property
    def payments(self):
        return get_table("payments")

    @property
    def providers(self):
        return get_table("providers")

    @property
    def log_table(self):
        return get_table("activity_log")

    @property
    def announcements_table(self):
        return get_table("in_app_notifications")

    def get_dashboard_stats(self, conn) -> dict:
        total_users = conn.execute(text("SELECT COUNT(*) FROM users WHERE role='CUSTOMER'")).scalar()
        total_providers = conn.execute(text("SELECT COUNT(*) FROM providers")).scalar()
        total_bookings = conn.execute(text("SELECT COUNT(*) FROM bookings")).scalar()
        pending_bookings = conn.execute(text("SELECT COUNT(*) FROM bookings WHERE status='PENDING'")).scalar()
        today_bookings = conn.execute(text("SELECT COUNT(*) FROM bookings WHERE DATE(created_at) = CURRENT_DATE")).scalar()
        total_revenue = conn.execute(text("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='PAID'")).scalar()
        pending_providers = conn.execute(text("SELECT COUNT(*) FROM providers WHERE status='PENDING'")).scalar()
        return {
            "total_customers": int(total_users or 0),
            "total_providers": int(total_providers or 0),
            "total_bookings": int(total_bookings or 0),
            "pending_bookings": int(pending_bookings or 0),
            "today_bookings": int(today_bookings or 0),
            "total_revenue_paise": int(total_revenue or 0),
            "pending_provider_approvals": int(pending_providers or 0),
        }

    def list_all_bookings(self, conn, status=None, page=1):
        sel = self.bookings.select().order_by(self.bookings.c.created_at.desc()).limit(20).offset((page-1)*20)
        if status:
            sel = sel.where(self.bookings.c.status == status)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def update_booking(self, conn, booking_id: str, fields: dict):
        fields["updated_at"] = now_utc()
        conn.execute(self.bookings.update().where(self.bookings.c.booking_id == booking_id).values(**fields))

    def list_users(self, conn, page=1):
        sel = self.users.select().where(self.users.c.role == "CUSTOMER").order_by(
            self.users.c.created_at.desc()
        ).limit(20).offset((page-1)*20)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_logs(self, conn, page=1):
        sel = self.log_table.select().order_by(
            self.log_table.c.created_at.desc()
        ).limit(20).offset((page-1)*20)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_announcements(self, conn, page=1):
        sel = self.announcements_table.select().where(
            self.announcements_table.c.type == "announcement"
        ).order_by(
            self.announcements_table.c.created_at.desc()
        ).limit(20).offset((page-1)*20)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]
