"""The customer picks when to pay while booking:
PAY_NOW   — online at booking; workers see the job only once it is paid.
PAY_AFTER — cash or online after the job; workers see the job at once."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tests.test_payments import PaymentTestBase
from bookings.bookings_service import is_dispatchable
from bookings.bookings_validator import CreateBookingSchema
from bookings.unpaid_expiry import expire_unpaid


class PaymentModeTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        # A fresh, unclaimed booking (the base fixture's b1 is already ACCEPTED).
        self.conn.exec_driver_sql(
            "UPDATE bookings SET provider_id=NULL, status='PENDING', payment_mode='PAY_NOW', created_at=? "
            "WHERE booking_id='b1'", (datetime.utcnow(),))

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

    def test_pay_after_never_expires(self):
        self.set_mode("PAY_AFTER")
        self.age(30)
        expire_unpaid(self.conn)
        self.assertEqual(self.booking()["status"], "PENDING")

    def test_recent_pay_now_does_not_expire(self):
        expire_unpaid(self.conn)
        self.assertEqual(self.booking()["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
