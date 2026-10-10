<<<<<<< HEAD
"""The customer picks when to pay while booking:
PAY_NOW   — online at booking; workers see the job only once it is paid.
PAY_AFTER — cash or online after the job; workers see the job at once."""
=======
"""A booking reaches workers only after it is paid (or costs nothing)."""
import os
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tests.test_payments import PaymentTestBase
from bookings.bookings_service import is_dispatchable
from bookings.bookings_validator import CreateBookingSchema
from bookings.unpaid_expiry import expire_unpaid


class PaymentModeTests(PaymentTestBase):
    def setUp(self):
<<<<<<< HEAD
        super().setUp()
        # A fresh, unclaimed booking (the base fixture's b1 is already ACCEPTED).
        self.conn.exec_driver_sql(
            "UPDATE bookings SET provider_id=NULL, status='PENDING', payment_mode='PAY_NOW', created_at=? "
            "WHERE booking_id='b1'", (datetime.utcnow(),))
=======
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
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943

    def booking(self):
        return self.books.modal.read_one(self.conn, "b1")

    def set_mode(self, mode):
        self.conn.exec_driver_sql("UPDATE bookings SET payment_mode=? WHERE booking_id='b1'", (mode,))

    def age(self, minutes):
        self.conn.exec_driver_sql("UPDATE bookings SET created_at=? WHERE booking_id='b1'",
                                  (datetime.utcnow() - timedelta(minutes=minutes),))

    def test_default_mode_is_pay_after(self):
        schema = CreateBookingSchema(service_id="s1", scheduled_at=datetime.utcnow() + timedelta(days=1))
        self.assertEqual(schema.payment_mode, "PAY_AFTER")

    def test_pay_now_unpaid_is_hidden_from_workers(self):
        self.assertFalse(is_dispatchable(self.booking()))
        self.assertFalse(self.books.modal.claim_booking(self.conn, "b1", "p1"))
        with self.assertRaises(PermissionError):
            self.books.get_detail({"_user_id": "wkr", "_role": "PROVIDER", "id": "b1"}, self.conn)

    def test_pay_now_dispatched_once_after_payment(self):
        with patch("bookings.bookings_service.BookingsService.dispatch") as dispatch:
            order = self.order()
            self.verify(order)
            self.verify(order)  # app retry / webhook replay
        self.assertEqual(dispatch.call_count, 1)
        self.assertTrue(is_dispatchable(self.booking()))
        self.assertTrue(self.books.modal.claim_booking(self.conn, "b1", "p1"))

<<<<<<< HEAD
    def test_pay_after_goes_to_workers_unpaid(self):
        self.set_mode("PAY_AFTER")
        self.assertTrue(is_dispatchable(self.booking()))
        self.assertTrue(self.books.modal.claim_booking(self.conn, "b1", "p1"))

    def test_pay_after_payment_does_not_redispatch(self):
        self.set_mode("PAY_AFTER")
        with patch("bookings.bookings_service.BookingsService.dispatch") as dispatch:
            self.verify(self.order())
        dispatch.assert_not_called()

    def test_free_booking_is_always_dispatchable(self):
        self.conn.exec_driver_sql("UPDATE bookings SET total_amount=0 WHERE booking_id='b1'")
        self.assertTrue(self.books.modal.claim_booking(self.conn, "b1", "p1"))
=======
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
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943

    def test_create_forces_pay_after_for_free_booking(self):
        pricing = {"sub_total": 0, "discount": 0, "total_amount": 0, "coupon": None,
                   "items": [], "coins_used": 0, "previous_dues": 0}
        with patch("bookings.bookings_service.price_booking", return_value=pricing), \
             patch("bookings.bookings_service.apply_booking_side_effects"), \
             patch("bookings.bookings_service.BookingsService.dispatch", side_effect=lambda c, b: b) as dispatch:
            _, created = self.books.create({
                "_user_id": "cust", "_role": "CUSTOMER", "service_id": "s1",
                "scheduled_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
                "payment_mode": "PAY_NOW",
            }, self.conn)
        self.assertEqual(created["payment_mode"], "PAY_AFTER")
        dispatch.assert_called_once()

    def test_unpaid_pay_now_expires(self):
        self.age(30)
        expire_unpaid(self.conn)
        self.assertEqual(self.booking()["status"], "CANCELLED")

<<<<<<< HEAD
    def test_pay_after_never_expires(self):
        self.set_mode("PAY_AFTER")
        self.age(30)
        expire_unpaid(self.conn)
        self.assertEqual(self.booking()["status"], "PENDING")

    def test_recent_pay_now_does_not_expire(self):
=======
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
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
        expire_unpaid(self.conn)
        self.assertEqual(self.booking()["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
