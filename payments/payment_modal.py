from sqlalchemy import text
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class PaymentMaster:
    @property
    def pay(self):
        return get_table("payments")

    @property
    def payout(self):
        return get_table("payout_requests")

    @property
    def earnings(self):
        return get_table("provider_earnings")

    def create_payment(self, conn, data: dict) -> dict:
        data["payment_id"] = new_uuid()
        data["created_at"] = now_utc()
        conn.execute(self.pay.insert().values(**data))
        return data

    def find_payment(self, conn, payment_id: str = None, razorpay_order_id: str = None, razorpay_payment_id: str = None):
        if not (payment_id or razorpay_order_id or razorpay_payment_id):
            return None
        sel = self.pay.select()
        if payment_id:
            sel = sel.where(self.pay.c.payment_id == payment_id)
        if razorpay_order_id:
            sel = sel.where(self.pay.c.razorpay_order_id == razorpay_order_id)
        if razorpay_payment_id:
            sel = sel.where(self.pay.c.razorpay_payment_id == razorpay_payment_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def find_open_order(self, conn, customer_id: str, amount: int, booking_id=None, plan_id=None):
        """A still-unpaid order for the same thing and amount — reused instead of
        opening a new Razorpay order on every tap of "Pay"."""
        sel = (self.pay.select()
               .where(self.pay.c.customer_id == customer_id)
               .where(self.pay.c.status == "PENDING")
               .where(self.pay.c.amount == amount)
               .order_by(self.pay.c.created_at.desc()))
        if booking_id:
            sel = sel.where(self.pay.c.booking_id == booking_id)
        else:
            sel = sel.where(self.pay.c.plan_id == plan_id).where(self.pay.c.purpose == "SUBSCRIPTION")
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def mark_paid(self, conn, payment_id: str, razorpay_payment_id: str, method=None) -> bool:
        """PENDING/FAILED → PAID exactly once (verify and webhook can race)."""
        fields = {"status": "PAID", "razorpay_payment_id": razorpay_payment_id, "paid_at": now_utc()}
        if method:
            fields["payment_method"] = method
        result = conn.execute(
            self.pay.update()
            .where(self.pay.c.payment_id == payment_id)
            .where(self.pay.c.status.in_(("PENDING", "FAILED")))
            .values(**fields)
        )
        return result.rowcount == 1

    def mark_failed(self, conn, payment_id: str):
        conn.execute(
            self.pay.update()
            .where(self.pay.c.payment_id == payment_id)
            .where(self.pay.c.status == "PENDING")
            .values(status="FAILED")
        )

    def reserve_refund(self, conn, payment: dict, amount: int) -> bool:
        """Add `amount` to refund_amount only if nobody refunded meanwhile."""
        already = int(payment.get("refund_amount") or 0)
        total = already + amount
        status = "REFUNDED" if total >= int(payment["amount"]) else "PARTIALLY_REFUNDED"
        result = conn.execute(
            self.pay.update()
            .where(self.pay.c.payment_id == payment["payment_id"])
            .where(self.pay.c.refund_amount == already)
            .where(self.pay.c.status.in_(("PAID", "PARTIALLY_REFUNDED", "REFUND_FAILED")))
            .values(refund_amount=total, status=status)
        )
        return result.rowcount == 1

    def undo_refund_reservation(self, conn, payment: dict, amount: int):
        """Razorpay refused the refund: put the amount back and flag it for admin."""
        conn.execute(
            self.pay.update()
            .where(self.pay.c.payment_id == payment["payment_id"])
            .values(refund_amount=self.pay.c.refund_amount - amount, status="REFUND_FAILED")
        )

    def undo_refund_by_id(self, conn, payment: dict, refund_id: str, amount: int) -> bool:
        """refund.failed webhook: only once per refund (Razorpay redelivers webhooks)."""
        result = conn.execute(
            self.pay.update()
            .where(self.pay.c.payment_id == payment["payment_id"])
            .where(self.pay.c.refund_id == refund_id)
            .values(refund_amount=self.pay.c.refund_amount - amount, refund_id=None, status="REFUND_FAILED")
        )
        return result.rowcount == 1

    def net_earning(self, conn, booking_id: str, provider_id: str) -> int:
        return int(conn.execute(text(
            "SELECT COALESCE(SUM(CASE WHEN type = 'BOOKING' THEN amount "
            "WHEN type = 'DEDUCTION' THEN -amount ELSE 0 END), 0) FROM provider_earnings "
            "WHERE booking_id = :bid AND provider_id = :pid"
        ), {"bid": booking_id, "pid": provider_id}).scalar() or 0)

    def update_payment(self, conn, payment_id: str, fields: dict):
        conn.execute(self.pay.update().where(self.pay.c.payment_id == payment_id).values(**fields))

    def create_payout_request(self, conn, data: dict) -> dict:
        data["payout_id"] = new_uuid()
        data["created_at"] = now_utc()
        conn.execute(self.payout.insert().values(**data))
        return data

    def list_payout_requests(self, conn, status=None, page=1):
        sel = self.payout.select().order_by(self.payout.c.created_at.desc()).limit(20).offset((page-1)*20)
        if status:
            sel = sel.where(self.payout.c.status == status)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def update_payout(self, conn, payout_id: str, fields: dict):
        conn.execute(self.payout.update().where(self.payout.c.payout_id == payout_id).values(**fields))

    def list_all_payments(self, conn, status=None, page=1):
        sel = self.pay.select().order_by(self.pay.c.created_at.desc()).limit(20).offset((page-1)*20)
        if status:
            sel = sel.where(self.pay.c.status == status)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def add_earning(self, conn, provider_id: str, booking_id: str, amount: int, earning_type: str = "BOOKING"):
        conn.execute(self.earnings.insert().values(
            earning_id=new_uuid(),
            provider_id=provider_id,
            booking_id=booking_id,
            amount=amount,
            type=earning_type,
            created_at=now_utc(),
        ))
