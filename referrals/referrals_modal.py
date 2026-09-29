from sqlalchemy import text
from utilities.db_connection import get_table
from utilities.common_table_elements import now_utc


class ReferralsMaster:
    @property
    def u(self):
        return get_table("users")

    @property
    def wl(self):
        return get_table("wallet_ledger")

    def count_referrals(self, conn, user_id: str) -> int:
        row = conn.execute(
            text("SELECT COUNT(*) FROM users WHERE referred_by = :uid"), {"uid": user_id}
        ).fetchone()
        return int(row[0] or 0)

    def sum_earned(self, conn, user_id: str) -> int:
        row = conn.execute(
            text("""
                SELECT COALESCE(SUM(delta), 0) FROM wallet_ledger
                WHERE user_id = :uid AND reason = 'REFERRAL_BONUS' AND delta > 0
            """),
            {"uid": user_id},
        ).fetchone()
        return int(row[0] or 0)

    def set_referred_by(self, conn, user_id: str, referrer_user_id: str):
        conn.execute(
            self.u.update().where(self.u.c.user_id == user_id)
            .values(referred_by=referrer_user_id, updated_at=now_utc())
        )

    def credit(self, conn, user_id: str, amount: int, reason: str, booking_id: str = None):
        conn.execute(self.wl.insert().values(
            user_id=user_id, delta=amount, reason=reason, booking_id=booking_id, created_at=now_utc(),
        ))
        conn.execute(
            self.u.update().where(self.u.c.user_id == user_id)
            .values(coins_balance=self.u.c.coins_balance + amount, updated_at=now_utc())
        )

    def debit(self, conn, user_id: str, amount: int, reason: str, booking_id: str = None) -> bool:
        """Atomically debit only if the balance actually covers it. Returns False if insufficient."""
        result = conn.execute(
            self.u.update()
            .where(self.u.c.user_id == user_id)
            .where(self.u.c.coins_balance >= amount)
            .values(coins_balance=self.u.c.coins_balance - amount, updated_at=now_utc())
        )
        if result.rowcount == 0:
            return False
        conn.execute(self.wl.insert().values(
            user_id=user_id, delta=-amount, reason=reason, booking_id=booking_id, created_at=now_utc(),
        ))
        return True

    def get_balance(self, conn, user_id: str) -> int:
        row = conn.execute(
            text("SELECT coins_balance FROM users WHERE user_id = :uid"), {"uid": user_id}
        ).fetchone()
        return int(row[0] or 0)
