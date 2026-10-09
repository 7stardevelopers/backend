"""A booking reaches workers only after it is paid (or costs nothing)."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from tests.helpers import make_db, insert
from bookings.bookings_service import BookingsService, is_dispatchable
from bookings.unpaid_expiry import expire_unpaid
from payments.payment_service import PaymentService


class PaymentGatingTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "s"})
        env.start()
        self.addCleanup(env.stop)
        self.conn = make_db()
        self.addCleanup(self.conn.close)
        insert(self.conn, "users", user_id="cust", role="CUSTOMER")
        insert(self.conn, "users", user_id="wkr", role="PROVIDER")
        insert(self.conn, "providers", provider_id="p1", user_id="wkr", status="APPROVED")
        insert(self.conn, "bookings", booking_id="b1", customer_id="cust", service_id="s1",
               status="PENDING", payment_status="PENDING", total_amount=49900,
               created_at=datetime.utcnow(), service_snapshot="{}")
        insert(self.conn, "payments", payment_id="pay1", booking_id="b1", customer_id="cust",
               razorpay_order_id="order_1", amount=49900, status="PENDING")
        self.svc = BookingsService()
        self.svc.notif = MagicMock()
        self.pay = PaymentService()
        self.pay.notif = MagicMock()

    def booking(self):
        return self.svc.modal.read_one(self.conn, "b1")

    def verify(self):
        with patch("payments.payment_service._verify_signature", return_value=True):
            return self.pay.verify_payment({
                "_user_id": "cust", "_role": "CUSTOMER", "booking_id": "b1",
                "razorpay_order_id": "order_1", "razorpay_payment_id": "pay_rz_1",
                "razorpay_signature": "sig",
            }, self.conn)[1]

    def test_unpaid_booking_cannot_be_claimed(self):
        self.assertFalse(is_dispatchable(self.booking()))
        self.assertFalse(self.svc.modal.claim_booking(self.conn, "b1", "p1"))

    def test_unpaid_booking_hidden_from_worker_detail(self):
        with self.assertRaises(PermissionError):
            self.svc.get_detail({"_user_id": "wkr", "_role": "PROVIDER", "id": "b1"}, self.conn)

    def test_free_booking_is_dispatchable(self):
        self.svc.modal.update_payment(self.conn, "b1", None, "PENDING")
        from utilities.db_connection import get_table
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "b1").values(total_amount=0))
        self.assertTrue(self.svc.modal.claim_booking(self.conn, "b1", "p1"))

    def test_verify_dispatches_once_then_worker_can_claim(self):
        with patch("bookings.bookings_service.BookingsService.dispatch") as dispatch:
            self.verify()
            self.verify()  # app retry — must not dispatch again
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(self.booking()["payment_status"], "PAID")
        self.assertTrue(self.svc.modal.claim_booking(self.conn, "b1", "p1"))

    def test_verify_on_cancelled_booking_refunds_and_does_not_dispatch(self):
        self.svc.modal.update_status(self.conn, "b1", "CANCELLED")
        client = MagicMock()
        client.payment.fetch.return_value = {"amount": 49900, "status": "captured", "method": "upi"}
        with patch("bookings.bookings_service.BookingsService.dispatch") as dispatch, \
             patch("payments.payment_service._get_razorpay_client", return_value=client):
            out = self.verify()
        dispatch.assert_not_called()
        self.assertTrue(out["refunded"])
        client.payment.refund.assert_called_once()
        rz_id, body = client.payment.refund.call_args.args
        self.assertEqual((rz_id, body["amount"]), ("pay_rz_1", 49900))

    def test_earning_credited_once_on_completion(self):
        self.verify_without_dispatch()
        self.svc.modal.claim_booking(self.conn, "b1", "p1")
        self.svc.modal.update_status(self.conn, "b1", "COMPLETED")
        self.pay.credit_provider_for_booking(self.conn, "b1")
        self.pay.credit_provider_for_booking(self.conn, "b1")
        wallet = self.conn.exec_driver_sql("SELECT wallet_balance FROM providers WHERE provider_id='p1'").scalar()
        self.assertEqual(wallet, 49900 - 4990)

    def verify_without_dispatch(self):
        with patch("bookings.bookings_service.BookingsService.dispatch"):
            self.verify()

    def test_stale_unpaid_booking_expires(self):
        from utilities.db_connection import get_table
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "b1")
                          .values(created_at=datetime.utcnow() - timedelta(minutes=30)))
        with patch("bookings.bookings_service.NotificationsService"), \
             patch("bookings.bookings_service.release_booking_side_effects"):
            expire_unpaid(self.conn)
        self.assertEqual(self.booking()["status"], "CANCELLED")

    def test_expiry_never_cancels_a_booking_paid_meanwhile(self):
        stale = self.booking()                       # expiry read it as unpaid…
        self.verify_without_dispatch()               # …then the payment landed
        with self.assertRaises(ValueError):
            self.svc._do_cancel(self.conn, stale, only_unpaid=True)
        self.assertEqual(self.booking()["status"], "PENDING")

    def test_paid_booking_never_expires(self):
        self.verify_without_dispatch()
        from utilities.db_connection import get_table
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "b1")
                          .values(created_at=datetime.utcnow() - timedelta(minutes=30)))
        expire_unpaid(self.conn)
        self.assertEqual(self.booking()["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
