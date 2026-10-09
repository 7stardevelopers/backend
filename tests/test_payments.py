"""Razorpay flow: idempotent verify/webhook, earnings on completion, refunds, plans. Amounts in paise."""
import hashlib
import hmac
import itertools
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from tests.helpers import make_db, insert
from bookings.bookings_service import BookingsService
from payments import payment_service
from payments.payment_service import PaymentService, PaymentsNotConfigured
from subscriptions.subscriptions_service import SubscriptionsService

SECRET = "test_key_secret"
WEBHOOK_SECRET = "test_webhook_secret"


def sign(order_id, payment_id, secret=SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


class PaymentTestBase(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": SECRET,
                                      "RAZORPAY_WEBHOOK_SECRET": WEBHOOK_SECRET})
        env.start()
        self.addCleanup(env.stop)
        push = patch("notifications.notifications_service.NotificationsService.send_push")
        self.push = push.start()
        self.addCleanup(push.stop)

        ids = itertools.count(1)
        self.rzp = MagicMock()
        self.rzp.order.create.side_effect = lambda body: {"id": f"order_{next(ids)}"}
        self.rzp.payment.fetch.side_effect = lambda pid: {"id": pid, "amount": self.paid_amount, "status": "captured", "method": "upi"}
        self.rzp.payment.refund.return_value = {"id": "rfnd_1"}
        client = patch.object(payment_service, "_get_razorpay_client", return_value=self.rzp)
        client.start()
        self.addCleanup(client.stop)
        self.paid_amount = 49900

        self.conn = make_db()
        self.addCleanup(self.conn.close)
        insert(self.conn, "users", user_id="cust", role="CUSTOMER", name="Asha")
        insert(self.conn, "users", user_id="wkr", role="PROVIDER", name="Ravi")
        insert(self.conn, "providers", provider_id="p1", user_id="wkr", status="APPROVED", wallet_balance=0)
        insert(self.conn, "bookings", booking_id="b1", customer_id="cust", provider_id="p1", status="ACCEPTED",
               total_amount=49900, platform_fee=4990, payment_status="PENDING", service_snapshot="{}")
        self.pay = PaymentService()
        self.books = BookingsService()

    # helpers
    def order(self, **target):
        target = target or {"booking_id": "b1"}
        return self.pay.create_order({"_user_id": "cust", "_role": "CUSTOMER", **target}, self.conn)[1]

    def verify(self, order, rzp_payment_id="pay_1", **target):
        target = target or {"booking_id": "b1"}
        return self.pay.verify_payment({
            "_user_id": "cust", "_role": "CUSTOMER", **target,
            "razorpay_order_id": order["razorpay_order_id"], "razorpay_payment_id": rzp_payment_id,
            "razorpay_signature": sign(order["razorpay_order_id"], rzp_payment_id),
        }, self.conn)[1]

    def row(self, table, key, value):
        r = self.conn.exec_driver_sql(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).mappings().fetchone()
        return dict(r) if r else None

    def wallet(self):
        return self.row("providers", "provider_id", "p1")["wallet_balance"]

    def complete(self):
        self.conn.exec_driver_sql("UPDATE bookings SET status='COMPLETED' WHERE booking_id='b1'")
        return self.pay.credit_provider_for_booking(self.conn, "b1")


class CheckoutTests(PaymentTestBase):
    def test_order_amount_from_db_in_inr(self):
        o = self.order(booking_id="b1", amount=1, currency="USD")
        self.assertEqual((o["amount"], o["currency"]), (49900, "INR"))
        body = self.rzp.order.create.call_args.args[0]
        self.assertEqual((body["amount"], body["currency"]), (49900, "INR"))

    def test_open_order_is_reused(self):
        self.assertEqual(self.order()["razorpay_order_id"], self.order()["razorpay_order_id"])
        self.assertEqual(self.rzp.order.create.call_count, 1)

    def test_cannot_pay_cancelled_or_paid_booking(self):
        self.verify(self.order())
        with self.assertRaisesRegex(ValueError, "already paid"):
            self.order()
        self.conn.exec_driver_sql("UPDATE bookings SET status='CANCELLED', payment_status='PENDING' WHERE booking_id='b1'")
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.order()

    def test_bad_signature_rejected(self):
        o = self.order()
        with self.assertRaisesRegex(ValueError, "signature"):
            self.pay.verify_payment({"_user_id": "cust", "_role": "CUSTOMER", "booking_id": "b1",
                                     "razorpay_order_id": o["razorpay_order_id"], "razorpay_payment_id": "pay_1",
                                     "razorpay_signature": sign(o["razorpay_order_id"], "pay_1", secret="")}, self.conn)

    def test_missing_secret_fails_closed(self):
        o = self.order()
        with patch.dict(os.environ, {"RAZORPAY_KEY_SECRET": ""}):
            with self.assertRaises(PaymentsNotConfigured):
                self.pay.verify_payment({"_user_id": "cust", "_role": "CUSTOMER", "booking_id": "b1",
                                         "razorpay_order_id": o["razorpay_order_id"], "razorpay_payment_id": "pay_1",
                                         "razorpay_signature": sign(o["razorpay_order_id"], "pay_1", secret="")}, self.conn)

    def test_amount_mismatch_rejected(self):
        self.paid_amount = 100
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.verify(self.order())

    def test_authorized_payment_gets_captured(self):
        self.rzp.payment.fetch.side_effect = lambda pid: {"amount": 49900, "status": "authorized", "method": "card"}
        self.verify(self.order())
        self.rzp.payment.capture.assert_called_once_with("pay_1", 49900, {"currency": "INR"})
        self.assertEqual(self.row("payments", "razorpay_payment_id", "pay_1")["payment_method"], "card")


class EarningsTests(PaymentTestBase):
    def test_verify_twice_credits_once_and_only_on_completion(self):
        o = self.order()
        self.verify(o)
        self.verify(o)
        self.assertEqual(self.row("bookings", "booking_id", "b1")["payment_status"], "PAID")
        self.assertEqual(self.wallet(), 0)          # job not done yet
        self.assertEqual(self.complete(), 44910)    # 49900 − 10% fee
        self.assertEqual(self.complete(), 0)        # never twice
        self.assertEqual(self.wallet(), 44910)

    def test_payment_confirmed_after_completion_credits_then(self):
        o = self.order()
        self.conn.exec_driver_sql("UPDATE bookings SET status='COMPLETED' WHERE booking_id='b1'")
        self.verify(o)
        self.assertEqual(self.wallet(), 44910)

    def test_no_online_payment_after_completion(self):
        # unpaid jobs are settled in cash when the work is done
        self.conn.exec_driver_sql("UPDATE bookings SET status='COMPLETED' WHERE booking_id='b1'")
        with self.assertRaisesRegex(ValueError, "completed"):
            self.order()

    def test_partial_refund_before_completion_credits_the_rest(self):
        self.verify(self.order())
        self.pay.request_refund({"_user_id": "adm", "_role": "ADMIN", "booking_id": "b1", "amount": 9900}, self.conn)
        # kept 40000 → fee 10% of it
        self.assertEqual(self.complete(), 36000)

    def test_refunded_booking_rejects_late_payment(self):
        stale = self.order()          # an older, still-open order
        self.conn.exec_driver_sql("UPDATE payments SET status='PENDING'")
        self.conn.exec_driver_sql("UPDATE bookings SET payment_status='REFUNDED' WHERE booking_id='b1'")
        self.assertTrue(self.verify(stale, "pay_late")["refunded"])
        self.assertEqual(self.row("bookings", "booking_id", "b1")["payment_status"], "REFUNDED")

    def test_extra_payment_on_paid_order_is_released_not_kept(self):
        o = self.order()
        self.verify(o, "pay_A")
        self.rzp.payment.fetch.side_effect = lambda pid: {"amount": 49900, "status": "captured"}
        out = self.verify(o, "pay_B")
        self.assertTrue(out["refunded"])
        self.rzp.payment.capture.assert_not_called()
        self.rzp.payment.refund.assert_called_once()
        self.assertEqual(self.rzp.payment.refund.call_args.args[0], "pay_B")
        self.assertEqual(self.row("payments", "razorpay_order_id", o["razorpay_order_id"])["razorpay_payment_id"], "pay_A")

    def test_cash_job_completion_credits_nothing(self):
        self.assertEqual(self.complete(), 0)

    def test_second_payment_is_refunded_not_credited(self):
        first = self.order()
        self.verify(first)
        # A second order made before the first was confirmed (other device / retry)
        self.conn.exec_driver_sql("UPDATE bookings SET payment_status='PENDING' WHERE booking_id='b1'")
        second = self.order()
        self.conn.exec_driver_sql("UPDATE bookings SET payment_status='PAID' WHERE booking_id='b1'")
        self.assertNotEqual(first["razorpay_order_id"], second["razorpay_order_id"])
        out = self.verify(second, "pay_2")
        self.assertTrue(out["refunded"])
        self.rzp.payment.refund.assert_called_once_with("pay_2", {"amount": 49900, "notes": {"reason": "Duplicate payment or cancelled booking"}})
        self.assertEqual(self.row("payments", "razorpay_payment_id", "pay_2")["status"], "REFUNDED")
        self.assertEqual(self.row("bookings", "booking_id", "b1")["payment_id"], first["payment_id"])

    def test_worker_gets_push_on_their_user_id(self):
        self.verify(self.order())
        pushed = [c.kwargs["user_ids"] for c in self.push.call_args_list]
        self.assertIn(["wkr"], pushed)
        self.assertNotIn(["p1"], pushed)


class RefundTests(PaymentTestBase):
    def test_cancel_refunds_and_takes_back_credit(self):
        self.verify(self.order())
        # credited earlier (e.g. under the old pay-time crediting)
        self.pay.modal.add_earning(self.conn, "p1", "b1", 44910)
        self.pay.provider_modal.update_wallet(self.conn, "p1", 44910)
        booking = self.row("bookings", "booking_id", "b1")
        self.books._do_cancel(self.conn, booking)
        self.assertEqual(self.row("payments", "razorpay_payment_id", "pay_1")["status"], "REFUNDED")
        self.assertEqual(self.row("bookings", "booking_id", "b1")["payment_status"], "REFUNDED")
        self.assertEqual(self.wallet(), 0)
        ded = self.conn.exec_driver_sql("SELECT amount FROM provider_earnings WHERE type='DEDUCTION'").fetchall()
        self.assertEqual(ded, [(44910,)])
        # cancelling can't claw back twice
        self.assertEqual(self.pay.reverse_provider_earning(self.conn, booking), 0)

    def test_failed_refund_on_cancel_is_flagged_not_lost(self):
        self.verify(self.order())
        self.rzp.payment.refund.side_effect = Exception("gateway down")
        self.books._do_cancel(self.conn, self.row("bookings", "booking_id", "b1"))
        p = self.row("payments", "razorpay_payment_id", "pay_1")
        self.assertEqual((p["status"], p["refund_amount"]), ("REFUND_FAILED", 0))
        self.assertEqual(self.row("bookings", "booking_id", "b1")["status"], "CANCELLED")

    def admin_refund(self, **kw):
        return self.pay.request_refund({"_user_id": "adm", "_role": "ADMIN", "booking_id": "b1", **kw}, self.conn)[1]

    def test_partial_then_rest_then_nothing(self):
        self.verify(self.order())
        self.assertEqual(self.admin_refund(amount=10000)["status"], "PARTIALLY_REFUNDED")
        with self.assertRaisesRegex(ValueError, "at most 39900"):
            self.admin_refund(amount=40000)
        out = self.admin_refund()
        self.assertEqual((out["refunded"], out["refund_amount"], out["status"]), (39900, 49900, "REFUNDED"))
        with self.assertRaises(ValueError):
            self.admin_refund()

    def test_admin_refund_error_is_readable(self):
        self.verify(self.order())
        self.rzp.payment.refund.side_effect = Exception("insufficient balance")
        with self.assertRaisesRegex(ValueError, "Razorpay refused"):
            self.admin_refund()


class WebhookTests(PaymentTestBase):
    def hook(self, event, secret=WEBHOOK_SECRET):
        raw = json.dumps(event)
        sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
        return self.pay.webhook({"_raw_body": raw, "_rzp_signature": sig}, self.conn)[1]

    def captured(self, order_id, pid="pay_1", amount=49900, status="captured"):
        return {"event": "payment.captured", "payload": {"payment": {"entity": {
            "id": pid, "order_id": order_id, "amount": amount, "status": status, "method": "upi"}}}}

    def test_bad_signature_rejected(self):
        with self.assertRaises(PermissionError):
            self.hook(self.captured(self.order()["razorpay_order_id"]), secret="wrong")

    def test_app_closed_after_paying_is_still_marked_paid(self):
        o = self.order()
        self.assertEqual(self.hook(self.captured(o["razorpay_order_id"]))["outcome"], "paid")
        self.assertEqual(self.row("bookings", "booking_id", "b1")["payment_status"], "PAID")
        # the app's late /verify is harmless
        self.verify(o)
        self.assertEqual(self.hook(self.captured(o["razorpay_order_id"]))["outcome"], "already")

    def test_wrong_amount_ignored(self):
        o = self.order()
        self.assertEqual(self.hook(self.captured(o["razorpay_order_id"], amount=1))["ignored"], "amount mismatch")
        self.assertEqual(self.row("bookings", "booking_id", "b1")["payment_status"], "PENDING")

    def test_refund_failed_webhook_applies_once(self):
        self.verify(self.order())
        self.pay.request_refund({"_user_id": "adm", "_role": "ADMIN", "booking_id": "b1"}, self.conn)
        ev = {"event": "refund.failed", "payload": {"refund": {"entity": {
            "id": "rfnd_1", "payment_id": "pay_1", "amount": 49900}}}}
        self.assertEqual(self.hook(ev)["outcome"], "refund_failed")
        self.assertEqual(self.hook(ev)["ignored"], "refund already handled")
        p = self.row("payments", "razorpay_payment_id", "pay_1")
        self.assertEqual((p["status"], p["refund_amount"]), ("REFUND_FAILED", 0))
        self.assertEqual(self.row("bookings", "booking_id", "b1")["payment_status"], "REFUND_FAILED")

    def test_extra_payment_via_webhook_released(self):
        o = self.order()
        self.verify(o, "pay_A")
        self.rzp.payment.fetch.side_effect = lambda pid: {"amount": 49900, "status": "authorized"}
        out = self.hook(self.captured(o["razorpay_order_id"], pid="pay_B", status="authorized"))
        self.assertEqual(out["outcome"], "extra_payment_released")
        self.rzp.payment.capture.assert_not_called()

    def test_payment_failed_then_retry_succeeds(self):
        o = self.order()
        self.hook({"event": "payment.failed", "payload": {"payment": {"entity": {"order_id": o["razorpay_order_id"]}}}})
        self.assertEqual(self.row("payments", "razorpay_order_id", o["razorpay_order_id"])["status"], "FAILED")
        self.verify(o, "pay_9")
        self.assertEqual(self.row("bookings", "booking_id", "b1")["payment_status"], "PAID")


class PlanPaymentTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        insert(self.conn, "subscription_plans", plan_id="pro", name="Pro", price=99900, discount_pct=30)
        insert(self.conn, "subscription_plans", plan_id="free", name="Free", price=0, discount_pct=0)
        self.paid_amount = 99900

    def active_plan(self):
        r = self.conn.exec_driver_sql("SELECT plan_id FROM user_subscriptions WHERE user_id='cust' AND status='ACTIVE'").fetchone()
        return r[0] if r else None

    def test_paid_plan_cannot_be_activated_for_free(self):
        with self.assertRaisesRegex(ValueError, "pay"):
            SubscriptionsService().subscribe({"_user_id": "cust", "_role": "CUSTOMER", "plan_id": "pro",
                                              "payment_id": "made-up"}, self.conn)
        self.assertIsNone(self.active_plan())

    def test_free_plan_still_direct(self):
        SubscriptionsService().subscribe({"_user_id": "cust", "_role": "CUSTOMER", "plan_id": "free"}, self.conn)
        self.assertEqual(self.active_plan(), "free")

    def test_plan_payment_activates_once(self):
        o = self.order(plan_id="pro")
        self.assertEqual(o["amount"], 99900)
        self.verify(o, plan_id="pro")
        self.verify(o, plan_id="pro")
        self.assertEqual(self.active_plan(), "pro")
        n = self.conn.exec_driver_sql("SELECT COUNT(*) FROM user_subscriptions WHERE user_id='cust'").scalar()
        self.assertEqual(n, 1)
        with self.assertRaisesRegex(ValueError, "already active"):
            self.order(plan_id="pro")

    def test_full_refund_turns_plan_off(self):
        o = self.order(plan_id="pro")
        self.verify(o, plan_id="pro")
        self.pay.request_refund({"_user_id": "adm", "_role": "ADMIN", "payment_id": o["payment_id"]}, self.conn)
        self.assertIsNone(self.active_plan())


if __name__ == "__main__":
    unittest.main()
