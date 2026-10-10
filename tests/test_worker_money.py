"""Rapido-style worker money: fee on cash jobs (dues + job block), late-cancel
fee carried to the next booking, and the twice-weekly payout batch. Paise."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from tests.helpers import insert
from tests.test_payments import PaymentTestBase, sign
from bookings.bookings_service import is_blocked_for_dues, cancellation_fee_for, cancel_free_until
from finance.auto_payouts import run_payout_batch, next_payout_date
from providers.providers_modal import ProvidersMaster

IST = timezone(timedelta(hours=5, minutes=30))
MONDAY_11_IST = datetime(2026, 10, 12, 11, 0, tzinfo=IST)   # 2026-10-12 is a Monday


class WorkerMoneyBase(PaymentTestBase):
    def setUp(self):
        super().setUp()
        insert(self.conn, "services", service_id="s1", base_price=10000, is_active=True)
        insert(self.conn, "users", user_id="wkr2", role="PROVIDER", name="Sita")
        insert(self.conn, "providers", provider_id="p2", user_id="wkr2", status="APPROVED", wallet_balance=0)
        self.conn.exec_driver_sql("UPDATE bookings SET service_id='s1' WHERE booking_id='b1'")

    def set(self, booking_id="b1", **values):
        cols = ", ".join(f"{k} = ?" for k in values)
        self.conn.exec_driver_sql(f"UPDATE bookings SET {cols} WHERE booking_id = ?", (*values.values(), booking_id))

    def wallet_of(self, pid):
        return self.row("providers", "provider_id", pid)["wallet_balance"]

    def booking(self, booking_id="b1"):
        return self.books.modal.read_one(self.conn, booking_id)

    def finish(self, booking_id="b1"):
        self.set(booking_id, status="COMPLETED")
        self.pay.settle_completed_booking(self.conn, booking_id)


class CashFeeTests(WorkerMoneyBase):
    def test_cash_job_charges_fee_once(self):
        self.finish()
        self.finish()  # second completion signal must not charge again
        self.assertEqual(self.wallet(), -4990)

    def test_cash_job_with_big_discount_credits_worker(self):
        # ₹600 job, ₹101 coupon → customer pays ₹499 cash; worker's share is ₹600 − ₹60.
        self.set(sub_total=60000, platform_fee=6000)
        self.finish()
        self.assertEqual(self.wallet(), (60000 - 6000) - 49900)

    def test_online_job_credits_share_not_fee(self):
        self.verify(self.order())
        self.finish()
        self.assertEqual(self.wallet(), 49900 - 4990)

    def test_online_payment_after_cash_settlement_reverses_fee(self):
        order = self.order()          # opened before completion
        self.finish()                 # settled as cash → −fee
        self.verify(order)            # …then the online payment lands
        self.assertEqual(self.wallet(), 49900 - 4990)

    def test_online_earnings_offset_dues(self):
        self.conn.exec_driver_sql("UPDATE providers SET wallet_balance = -4990 WHERE provider_id='p1'")
        self.verify(self.order())
        self.finish()
        self.assertEqual(self.wallet(), 49900 - 4990 - 4990)


class DuesLimitTests(WorkerMoneyBase):
    def setUp(self):
        super().setUp()
        self.conn.exec_driver_sql("UPDATE providers SET wallet_balance = -50000 WHERE provider_id='p1'")
        insert(self.conn, "provider_services", provider_id="p1", service_id="s1")
        insert(self.conn, "provider_services", provider_id="p2", service_id="s1")

    def test_blocked_worker_gets_no_jobs(self):
        p1 = ProvidersMaster().find_by_id(self.conn, "p1")
        self.assertTrue(is_blocked_for_dues(p1))
        ids = [p["provider_id"] for p in ProvidersMaster().get_available_for_service(self.conn, "s1")]
        self.assertEqual(ids, ["p2"])
        _, feed = self.books.list_available_for_provider({"_user_id": "wkr", "_role": "PROVIDER"}, self.conn)
        self.assertEqual(feed, [])
        self.set(status="PENDING", provider_id=None)
        with self.assertRaises(PermissionError):
            self.books.accept_booking({"_user_id": "wkr", "_role": "PROVIDER", "id": "b1"}, self.conn)

    def test_paying_dues_unblocks_once(self):
        self.paid_amount = 50000
        order = self.pay.dues_order({"_user_id": "wkr", "_role": "PROVIDER"}, self.conn)[1]
        self.assertEqual(order["amount"], 50000)
        body = {"_user_id": "wkr", "_role": "PROVIDER", "razorpay_order_id": order["razorpay_order_id"],
                "razorpay_payment_id": "pay_d1", "razorpay_signature": sign(order["razorpay_order_id"], "pay_d1")}
        self.pay.dues_verify(dict(body), self.conn)
        self.pay.dues_verify(dict(body), self.conn)  # app retry
        self.assertEqual(self.wallet(), 0)
        self.assertFalse(is_blocked_for_dues(ProvidersMaster().find_by_id(self.conn, "p1")))

    def test_dues_order_needs_dues(self):
        self.conn.exec_driver_sql("UPDATE providers SET wallet_balance = 100 WHERE provider_id='p1'")
        with self.assertRaises(ValueError):
            self.pay.dues_order({"_user_id": "wkr", "_role": "PROVIDER"}, self.conn)

    def test_earnings_stats_show_dues(self):
        stats = ProvidersMaster().get_earnings(self.conn, "p1")["stats"]
        self.assertEqual((stats["dues"], stats["jobs_blocked"]), (50000, True))


class CancelFeeTests(WorkerMoneyBase):
    def cancel(self, booking_id="b1"):
        return self.books.cancel({"_user_id": "cust", "_role": "CUSTOMER", "id": booking_id}, self.conn)[1]

    def new_booking(self):
        return self.books.create({
            "_user_id": "cust", "_role": "CUSTOMER", "service_id": "s1",
            "scheduled_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
        }, self.conn)[1]

    def test_free_within_grace(self):
        self.set(accepted_at=datetime.utcnow() - timedelta(minutes=2))
        self.assertEqual(self.cancel()["cancellation_fee"], 0)
        self.assertIsNone(self.booking()["cancel_fee_status"])

    def test_free_before_assignment(self):
        self.set(status="PENDING", provider_id=None)
        self.assertEqual(self.cancel()["cancellation_fee"], 0)

    def test_detail_preview(self):
        self.set(accepted_at=datetime.utcnow() - timedelta(minutes=10))
        self.assertEqual(cancellation_fee_for(self.booking()), 5000)
        self.assertTrue(cancel_free_until(self.booking()).endswith("Z"))

    def test_late_unpaid_cancel_goes_to_next_booking_and_worker(self):
        self.set(accepted_at=datetime.utcnow() - timedelta(minutes=10))
        self.assertEqual(self.cancel()["cancellation_fee"], 5000)
        self.assertEqual(self.booking()["cancel_fee_status"], "DUE")

        nxt = self.new_booking()
        self.assertEqual((nxt["total_amount"], nxt["dues_collected"], nxt["platform_fee"]), (15000, 5000, 1000))
        self.assertEqual(self.booking()["cancel_fee_status"], "COLLECTING")

        self.cancel(nxt["booking_id"])            # still finding an expert → free…
        self.assertEqual(self.booking()["cancel_fee_status"], "DUE")   # …and the fee is owed again

        nxt = self.new_booking()
        self.set(nxt["booking_id"], provider_id="p2")
        self.finish(nxt["booking_id"])            # paid in cash to worker 2
        self.assertEqual(self.booking()["cancel_fee_status"], "COLLECTED")
        self.assertEqual(self.wallet_of("p1"), 5000)            # cancelled job's worker gets the fee
        self.assertEqual(self.wallet_of("p2"), -(1000 + 5000))  # worker 2 holds it in cash → owes it

    def test_late_cancel_after_online_payment_keeps_fee(self):
        self.verify(self.order())
        self.set(accepted_at=datetime.utcnow() - timedelta(minutes=10))
        self.cancel()
        self.rzp.payment.refund.assert_called_once()
        self.assertEqual(self.rzp.payment.refund.call_args[0][1]["amount"], 49900 - 5000)
        self.assertEqual(self.wallet(), 5000)
        self.assertEqual(self.booking()["cancel_fee_status"], "COLLECTED")

    def test_admin_cancel_is_free(self):
        self.set(accepted_at=datetime.utcnow() - timedelta(minutes=10))
        out = self.books.cancel({"_user_id": "admin", "_role": "ADMIN", "id": "b1"}, self.conn)[1]
        self.assertEqual(out["cancellation_fee"], 0)


class PayoutBatchTests(WorkerMoneyBase):
    def setUp(self):
        super().setUp()
        self.conn.exec_driver_sql("UPDATE providers SET wallet_balance = 30000, bank_account_number='123', "
                                  "bank_ifsc='HDFC0001' WHERE provider_id='p1'")
        self.conn.exec_driver_sql("UPDATE providers SET wallet_balance = 30000 WHERE provider_id='p2'")  # no bank
        self.notif = MagicMock()

    def payouts(self):
        return self.conn.exec_driver_sql("SELECT provider_id, amount, status FROM payout_requests").fetchall()

    def test_runs_on_payout_day_once(self):
        self.assertEqual(run_payout_batch(self.conn, MONDAY_11_IST, self.notif), 1)
        self.assertEqual(run_payout_batch(self.conn, MONDAY_11_IST + timedelta(hours=1), self.notif), 0)
        self.assertEqual([tuple(r) for r in self.payouts()], [("p1", 30000, "APPROVED")])

    def test_not_on_other_days_or_too_early(self):
        self.assertEqual(run_payout_batch(self.conn, MONDAY_11_IST + timedelta(days=1), self.notif), 0)
        self.assertEqual(run_payout_batch(self.conn, MONDAY_11_IST.replace(hour=8), self.notif), 0)

    def test_skips_small_balance_and_reserved_money(self):
        insert(self.conn, "payout_requests", payout_id="old", provider_id="p1", amount=15000, status="PENDING")
        self.assertEqual(run_payout_batch(self.conn, MONDAY_11_IST, self.notif), 0)  # 15000 left < 20000

    def test_next_payout_date(self):
        self.assertEqual(next_payout_date(MONDAY_11_IST).isoformat(), "2026-10-15")              # → Thursday
        self.assertEqual(next_payout_date(MONDAY_11_IST.replace(hour=8)).isoformat(), "2026-10-12")


if __name__ == "__main__":
    unittest.main()
