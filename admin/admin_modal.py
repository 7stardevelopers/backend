from sqlalchemy import text, func
from utilities.db_connection import get_table
from utilities.common_table_elements import now_utc, new_uuid


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

    # ── NEW ADMIN METHODS (additive) ────────────────────────────────

    def dashboard_overview(self, conn) -> dict:
        def _count(sql):
            return int(conn.execute(text(sql)).scalar() or 0)
        return {
            "total_customers":            _count("SELECT COUNT(*) FROM users WHERE role='CUSTOMER'"),
            "total_providers":            _count("SELECT COUNT(*) FROM providers"),
            "total_bookings":             _count("SELECT COUNT(*) FROM bookings"),
            "pending_bookings":           _count("SELECT COUNT(*) FROM bookings WHERE status='PENDING'"),
            "completed_bookings":         _count("SELECT COUNT(*) FROM bookings WHERE status='COMPLETED'"),
            "cancelled_bookings":         _count("SELECT COUNT(*) FROM bookings WHERE status='CANCELLED'"),
            "today_bookings":             _count("SELECT COUNT(*) FROM bookings WHERE DATE(created_at)=CURDATE()"),
            "total_revenue_paise":        int(conn.execute(text("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='PAID'")).scalar() or 0),
            "pending_provider_approvals": _count("SELECT COUNT(*) FROM providers WHERE status='PENDING'"),
        }

    def list_bookings_detailed(self, conn, status=None, page=1, per_page=20):
        where  = "WHERE b.status = :status" if status else ""
        params = {"lim": per_page, "off": (page - 1) * per_page}
        if status:
            params["status"] = status
        sql = f"""
            SELECT b.booking_id, b.status, b.scheduled_at, b.total_amount,
                   b.payment_status, b.created_at,
                   cu.name  AS customer_name, cu.phone AS customer_phone,
                   pu.name  AS provider_name,
                   s.name   AS service_name
            FROM bookings b
            LEFT JOIN users     cu ON b.customer_id = cu.user_id
            LEFT JOIN providers pr ON b.provider_id = pr.provider_id
            LEFT JOIN users     pu ON pr.user_id    = pu.user_id
            LEFT JOIN services  s  ON b.service_id  = s.service_id
            {where}
            ORDER BY b.created_at DESC
            LIMIT :lim OFFSET :off
        """
        count_sql = f"SELECT COUNT(*) FROM bookings b {where}"
        rows  = conn.execute(text(sql), params).mappings().fetchall()
        total = int(conn.execute(text(count_sql), {"status": status} if status else {}).scalar() or 0)
        return {"items": [dict(r) for r in rows], "total": total}

    def list_users_detailed(self, conn, page=1, per_page=20, search=None):
        where  = "WHERE role='CUSTOMER'"
        params = {"lim": per_page, "off": (page - 1) * per_page}
        if search:
            where += " AND (name LIKE :q OR phone LIKE :q)"
            params["q"] = f"%{search}%"
        sql = f"""
            SELECT user_id AS id, name, phone, email, photo_url, role, status, created_at
            FROM users {where}
            ORDER BY created_at DESC
            LIMIT :lim OFFSET :off
        """
        count_sql = f"SELECT COUNT(*) FROM users {where}"
        rows  = conn.execute(text(sql), params).mappings().fetchall()
        total = int(conn.execute(text(count_sql), {"q": params["q"]} if search else {}).scalar() or 0)
        return {"items": [dict(r) for r in rows], "total": total}

    def list_all_services(self, conn):
        sql = """
            SELECT s.*, c.name AS category_name
            FROM services s
            LEFT JOIN categories c ON s.category_id = c.category_id
            ORDER BY s.sort_order, s.name
        """
        return [dict(r) for r in conn.execute(text(sql)).mappings().fetchall()]

    def list_payments(self, conn, status=None, page=1, per_page=20):
        where  = "WHERE pay.status = :status" if status else ""
        params = {"lim": per_page, "off": (page - 1) * per_page}
        if status:
            params["status"] = status
        sql = f"""
            SELECT pay.payment_id, pay.amount, pay.status, pay.payment_method,
                   pay.created_at, pay.razorpay_payment_id, pay.refund_amount,
                   b.booking_id, b.status AS booking_status,
                   cu.name AS customer_name,
                   s.name  AS service_name
            FROM payments pay
            LEFT JOIN bookings b ON pay.booking_id  = b.booking_id
            LEFT JOIN users   cu ON pay.customer_id = cu.user_id
            LEFT JOIN services s ON b.service_id    = s.service_id
            {where}
            ORDER BY pay.created_at DESC
            LIMIT :lim OFFSET :off
        """
        count_sql = f"SELECT COUNT(*) FROM payments pay {where}"
        stats_sql = """
            SELECT
              COALESCE(SUM(CASE WHEN status='PAID'     THEN amount       ELSE 0 END), 0) AS total_revenue,
              COALESCE(SUM(CASE WHEN status='REFUNDED' THEN refund_amount ELSE 0 END), 0) AS total_refunded,
              COUNT(CASE WHEN status='PAID'    THEN 1 END) AS paid_count,
              COUNT(CASE WHEN status='PENDING' THEN 1 END) AS pending_count
            FROM payments
        """
        rows  = conn.execute(text(sql), params).mappings().fetchall()
        total = int(conn.execute(text(count_sql), {"status": status} if status else {}).scalar() or 0)
        stats = dict(conn.execute(text(stats_sql)).mappings().one())
        return {"items": [dict(r) for r in rows], "total": total, "stats": stats}

    def list_reviews_detailed(self, conn, rating=None, page=1, per_page=20):
        where  = "WHERE r.is_deleted = FALSE"
        params = {"lim": per_page, "off": (page - 1) * per_page}
        if rating:
            where += " AND r.rating = :rating"
            params["rating"] = int(rating)
        sql = f"""
            SELECT r.review_id, r.rating, r.comment, r.created_at,
                   cu.name AS from_user_name,
                   pu.name AS to_user_name,
                   s.name  AS service_name,
                   r.provider_id, r.service_id
            FROM reviews r
            LEFT JOIN users     cu ON r.customer_id = cu.user_id
            LEFT JOIN providers pr ON r.provider_id = pr.provider_id
            LEFT JOIN users     pu ON pr.user_id    = pu.user_id
            LEFT JOIN services  s  ON r.service_id  = s.service_id
            {where}
            ORDER BY r.created_at DESC
            LIMIT :lim OFFSET :off
        """
        count_sql = f"SELECT COUNT(*) FROM reviews r {where}"
        rows  = conn.execute(text(sql), params).mappings().fetchall()
        cp    = {"rating": int(rating)} if rating else {}
        total = int(conn.execute(text(count_sql), cp).scalar() or 0)
        return {"items": [dict(r) for r in rows], "total": total}

    def create_category(self, conn, data: dict) -> dict:
        t   = get_table("categories")
        uid = new_uuid()[:8]
        row = {
            "category_id": uid,
            "name":        data["name"],
            "icon":        data.get("icon"),
            "color":       data.get("color"),
            "sort_order":  data.get("sort_order", 0),
            "is_active":   data.get("is_active", True),
        }
        conn.execute(t.insert().values(**row))
        return row

    def update_category(self, conn, category_id: str, fields: dict):
        t = get_table("categories")
        conn.execute(t.update().where(t.c.category_id == category_id).values(**fields))

    def list_subscription_plans_all(self, conn, page=1, per_page=20):
        sql = """
            SELECT sp.*,
              (SELECT COUNT(*) FROM user_subscriptions us
               WHERE us.plan_id = sp.plan_id AND us.status='ACTIVE') AS subscriber_count
            FROM subscription_plans sp
            ORDER BY sp.sort_order
            LIMIT :lim OFFSET :off
        """
        count_sql = "SELECT COUNT(*) FROM subscription_plans"
        params = {"lim": per_page, "off": (page - 1) * per_page}
        rows  = conn.execute(text(sql), params).mappings().fetchall()
        total = int(conn.execute(text(count_sql)).scalar() or 0)
        return {"items": [dict(r) for r in rows], "total": total}

    def create_subscription_plan(self, conn, data: dict) -> dict:
        t = get_table("subscription_plans")
        conn.execute(t.insert().values(
            name              = data["name"],
            price             = data["price"],
            bookings_included = data.get("bookings_included"),
            discount_pct      = data.get("discount_pct", 0),
            is_active         = data.get("is_active", True),
            sort_order        = data.get("sort_order", 0),
        ))
        row = conn.execute(
            t.select().where(t.c.name == data["name"]).order_by(t.c.sort_order.desc()).limit(1)
        ).fetchone()
        return dict(row._mapping) if row else {}

    def update_subscription_plan(self, conn, plan_id: str, fields: dict):
        t = get_table("subscription_plans")
        conn.execute(t.update().where(t.c.plan_id == plan_id).values(**fields))

    def create_announcement(self, conn, data: dict) -> dict:
        t   = get_table("announcements")
        uid = new_uuid()
        row = {
            "announcement_id": uid,
            "title":           data["title"],
            "body":            data["body"],
            "target_role":     data.get("target_role", "ALL"),
            "sent_by":         data.get("sent_by"),
            "sent_at":         now_utc(),
        }
        conn.execute(t.insert().values(**row))
        return row

    def list_announcements(self, conn, limit=50):
        sql = "SELECT * FROM announcements ORDER BY sent_at DESC LIMIT :lim"
        return [dict(r) for r in conn.execute(text(sql), {"lim": limit}).mappings().fetchall()]

    def list_logs(self, conn, page=1, per_page=20, search=None):
        where  = ""
        params = {"lim": per_page, "off": (page - 1) * per_page}
        if search:
            where = "WHERE al.action LIKE :q OR al.entity_type LIKE :q"
            params["q"] = f"%{search}%"
        sql = f"""
            SELECT al.log_id       AS id,
                   al.user_id      AS admin_id,
                   u.name          AS admin_name,
                   al.action,
                   al.entity_type  AS entity,
                   al.entity_id,
                   al.metadata     AS changes,
                   al.ip_address   AS ip,
                   al.created_at
            FROM activity_log al
            LEFT JOIN users u ON al.user_id = u.user_id
            {where}
            ORDER BY al.created_at DESC
            LIMIT :lim OFFSET :off
        """
        count_sql = f"SELECT COUNT(*) FROM activity_log al {where}"
        rows  = conn.execute(text(sql), params).mappings().fetchall()
        cp    = {"q": params["q"]} if search else {}
        total = int(conn.execute(text(count_sql), cp).scalar() or 0)
        return {"items": [dict(r) for r in rows], "total": total}

    def write_log(self, conn, user_id, action, entity_type, entity_id=None, metadata=None):
        t = get_table("activity_log")
        conn.execute(t.insert().values(
            log_id      = new_uuid(),
            user_id     = user_id,
            action      = action,
            entity_type = entity_type,
            entity_id   = entity_id,
            metadata    = metadata,
            created_at  = now_utc(),
        ))
