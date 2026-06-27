from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc
from datetime import datetime, timedelta, timezone


class SubscriptionsMaster:
    @property
    def plans(self):
        return get_table("subscription_plans")

    @property
    def subs(self):
        return get_table("user_subscriptions")

    def list_plans(self, conn):
        sel = self.plans.select().where(self.plans.c.is_active == True).order_by(self.plans.c.sort_order)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_plan(self, conn, plan_id: str):
        sel = self.plans.select().where(self.plans.c.plan_id == plan_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def get_active_subscription(self, conn, user_id: str):
        now = datetime.now(timezone.utc)
        sel = self.subs.select().where(
            self.subs.c.user_id == user_id
        ).where(self.subs.c.status == "ACTIVE").where(self.subs.c.expires_at > now)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def create_subscription(self, conn, user_id: str, plan_id: str, payment_id: str = None) -> dict:
        starts_at = datetime.now(timezone.utc)
        expires_at = starts_at + timedelta(days=30)
        existing = self.get_active_subscription(conn, user_id)
        if existing:
            conn.execute(
                self.subs.update().where(self.subs.c.subscription_id == existing["subscription_id"])
                .values(status="CANCELLED")
            )
        sub = {
            "subscription_id": new_uuid(),
            "user_id": user_id,
            "plan_id": plan_id,
            "status": "ACTIVE",
            "starts_at": starts_at,
            "expires_at": expires_at,
            "bookings_used": 0,
            "payment_id": payment_id,
            "created_at": now_utc(),
        }
        conn.execute(self.subs.insert().values(**sub))
        return sub

    def cancel_subscription(self, conn, user_id: str):
        sub = self.get_active_subscription(conn, user_id)
        if not sub:
            raise ValueError("No active subscription found")
        conn.execute(
            self.subs.update().where(self.subs.c.subscription_id == sub["subscription_id"])
            .values(status="CANCELLED")
        )
        return sub

    def increment_bookings_used(self, conn, user_id: str):
        sub = self.get_active_subscription(conn, user_id)
        if sub:
            conn.execute(
                self.subs.update().where(self.subs.c.subscription_id == sub["subscription_id"])
                .values(bookings_used=sub["bookings_used"] + 1)
            )
