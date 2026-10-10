import os
from sqlalchemy import text
from utilities.db_connection import get_table


class FinanceMaster:
    @property
    def pay(self):
        return get_table("payments")

    @property
    def payout(self):
        return get_table("payout_requests")

    @property
    def earnings(self):
        return get_table("provider_earnings")

    def get_overview(self, conn) -> dict:
        gmv = conn.execute(text("SELECT COALESCE(SUM(amount - COALESCE(refund_amount,0)),0) FROM payments "
                         "WHERE status IN ('PAID','PARTIALLY_REFUNDED','REFUND_FAILED')")).scalar()
        platform_fee_pct = float(os.environ.get("PLATFORM_FEE_PCT", "10"))
        platform_fees = int((gmv or 0) * platform_fee_pct / 100)
        total_payouts = conn.execute(text("SELECT COALESCE(SUM(amount),0) FROM payout_requests WHERE status='PROCESSED'")).scalar()
        pending_payouts = conn.execute(text("SELECT COALESCE(SUM(amount),0) FROM payout_requests WHERE status='PENDING'")).scalar()
        total_txn = conn.execute(text("SELECT COUNT(*) FROM payments WHERE status='PAID'")).scalar()
        return {
            "gmv_paise": int(gmv or 0),
            "platform_fees_paise": platform_fees,
            "total_payouts_processed_paise": int(total_payouts or 0),
            "pending_payouts_paise": int(pending_payouts or 0),
            "total_transactions": int(total_txn or 0),
        }

    def list_payout_queue(self, conn, status="PENDING", page=1, per_page=50) -> list:
        """Payouts with the worker's name/phone so admin can match the bank transfer."""
        rows = conn.execute(text("""
            SELECT pr.*, u.name AS provider_name, u.phone AS provider_phone,
                   p.bank_account_name AS bank_account_name
            FROM payout_requests pr
            JOIN providers p ON p.provider_id = pr.provider_id
            LEFT JOIN users u ON u.user_id = p.user_id
            WHERE pr.status = :status
            ORDER BY pr.created_at DESC
            LIMIT :lim OFFSET :off
        """), {"status": status, "lim": per_page, "off": (page - 1) * per_page}).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_payout(self, conn, payout_id: str):
        row = conn.execute(self.payout.select().where(self.payout.c.payout_id == payout_id)).fetchone()
        return dict(row._mapping) if row else None

    def update_payout(self, conn, payout_id: str, fields: dict, expected_status=None) -> bool:
        from utilities.common_table_elements import now_utc
        fields["processed_at"] = now_utc()
        upd = self.payout.update().where(self.payout.c.payout_id == payout_id)
        if expected_status is not None:
            upd = upd.where(self.payout.c.status == expected_status)
        return conn.execute(upd.values(**fields)).rowcount > 0

    def get_report(self, conn, from_date: str = None, to_date: str = None) -> list:
        query = "SELECT p.*, b.booking_id FROM payments p LEFT JOIN bookings b ON p.booking_id = b.booking_id WHERE p.status='PAID'"
        params = {}
        if from_date:
            query += " AND p.created_at >= :from_date"
            params["from_date"] = from_date
        if to_date:
            query += " AND p.created_at <= :to_date"
            params["to_date"] = to_date
        query += " ORDER BY p.created_at DESC LIMIT 500"
        result = conn.execute(text(query), params)
        return [dict(r._mapping) for r in result.fetchall()]
