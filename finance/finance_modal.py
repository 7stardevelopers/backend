from sqlalchemy import text
from utilities.db_connection import metadata


class FinanceMaster:
    def __init__(self):
        self.pay = metadata.tables["payments"]
        self.payout = metadata.tables["payout_requests"]
        self.earnings = metadata.tables["provider_earnings"]

    def get_overview(self, conn) -> dict:
        gmv = conn.execute(text("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='PAID'")).scalar()
        platform_fee_pct = 10
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

    def list_payout_queue(self, conn, status="PENDING", page=1) -> list:
        sel = self.payout.select().where(self.payout.c.status == status).order_by(
            self.payout.c.created_at.desc()
        ).limit(20).offset((page-1)*20)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def update_payout(self, conn, payout_id: str, fields: dict):
        from utilities.common_table_elements import now_utc
        fields["processed_at"] = now_utc()
        conn.execute(self.payout.update().where(self.payout.c.payout_id == payout_id).values(**fields))

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
