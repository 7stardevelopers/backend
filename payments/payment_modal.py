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

    def find_payment(self, conn, payment_id: str = None, razorpay_order_id: str = None):
        sel = self.pay.select()
        if payment_id:
            sel = sel.where(self.pay.c.payment_id == payment_id)
        if razorpay_order_id:
            sel = sel.where(self.pay.c.razorpay_order_id == razorpay_order_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

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

    def add_earning(self, conn, provider_id: str, booking_id: str, amount: int, earning_type: str = "BOOKING"):
        conn.execute(self.earnings.insert().values(
            earning_id=new_uuid(),
            provider_id=provider_id,
            booking_id=booking_id,
            amount=amount,
            type=earning_type,
            created_at=now_utc(),
        ))
