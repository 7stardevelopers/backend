"""A job completes only when BOTH the worker and the customer tap Done."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from tests.helpers import make_db, insert
from bookings.bookings_service import BookingsService
from bookings.completion_reminders import remind_unconfirmed
from bookings.live_tracking import is_live_tracking
from providers.provider_matching import _busy_provider_ids


class CompletionTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.addCleanup(self.conn.close)
        insert(self.conn, "users", user_id="cust", role="CUSTOMER", name="Asha")
        insert(self.conn, "users", user_id="wkr", role="PROVIDER", name="Ravi")
        insert(self.conn, "users", user_id="other", role="CUSTOMER", name="X")
        insert(self.conn, "providers", provider_id="p1", user_id="wkr", status="APPROVED")
        insert(self.conn, "bookings", booking_id="b1", customer_id="cust", provider_id="p1",
               status="IN_PROGRESS", service_snapshot="{}")
        self.svc = BookingsService()
        self.svc.notif = MagicMock()

    def worker_done(self, user="wkr", role="PROVIDER"):
        return self.svc.complete({"_user_id": user, "_role": role, "id": "b1",
                                  "proof_photos": ["https://s3/proof.jpg"]}, self.conn)[1]

    def customer_done(self, user="cust"):
        return self.svc.confirm_complete({"_user_id": user, "_role": "CUSTOMER", "id": "b1"}, self.conn)[1]

    def report(self, message="Tap still leaking"):
        return self.svc.report_problem({"_user_id": "cust", "_role": "CUSTOMER", "id": "b1",
                                        "message": message}, self.conn)[1]

    def booking(self):
        return self.svc.modal.read_one(self.conn, "b1")

    def pushes(self):
        """[(user_ids, data_type)] of every push sent."""
        return [(c.kwargs["user_ids"], c.kwargs["data"]["type"]) for c in self.svc.notif.send_push.call_args_list]

    def set(self, **values):
        from utilities.db_connection import get_table
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "b1").values(**values))


class TwoSidedCompletionTests(CompletionTestBase):
    def test_worker_done_alone_does_not_complete(self):
        out = self.worker_done()
        self.assertEqual(out["status"], "IN_PROGRESS")
        b = self.booking()
        self.assertEqual(b["status"], "IN_PROGRESS")
        self.assertIsNotNone(b["provider_done_at"])
        self.assertIn((["cust"], "completion_requested"), self.pushes())

    def test_customer_done_alone_does_not_complete(self):
        out = self.customer_done()
        self.assertEqual(out["waiting_for"], "provider")
        self.assertEqual(self.booking()["status"], "IN_PROGRESS")
        self.assertIn((["wkr"], "completion_confirmed"), self.pushes())

    def test_worker_then_customer_completes(self):
        self.worker_done()
        self.assertEqual(self.customer_done()["status"], "COMPLETED")
        self.assertEqual(self.booking()["status"], "COMPLETED")

    def test_customer_then_worker_completes(self):
        self.customer_done()
        self.assertEqual(self.worker_done()["status"], "COMPLETED")
        self.assertEqual(self.booking()["status"], "COMPLETED")

    def test_completed_notifications_sent_once(self):
        self.worker_done()
        self.customer_done()
        updates = [p for p in self.pushes() if p[1] == "booking_update"]
        self.assertEqual(sorted(u[0][0] for u in updates), ["cust", "wkr"])  # one each

    def test_try_finish_only_wins_once(self):
        self.worker_done()
        self.customer_done()
        self.assertFalse(self.svc.modal.try_finish(self.conn, "b1"))

    def test_each_side_can_only_tap_once(self):
        self.worker_done()
        with self.assertRaises(ValueError):
            self.worker_done()
        self.customer_done()  # completes
        with self.assertRaises(ValueError):
            self.customer_done()

    def test_wrong_people_blocked(self):
        with self.assertRaises(PermissionError):
            self.customer_done(user="other")
        with self.assertRaises(PermissionError):
            self.customer_done(user="wkr")           # the worker can't confirm for the customer
        with self.assertRaises(PermissionError):
            self.worker_done(user="cust", role="CUSTOMER")

    def test_only_while_in_progress(self):
        self.set(status="EN_ROUTE")
        with self.assertRaises(ValueError):
            self.customer_done()
        with self.assertRaises(ValueError):
            self.worker_done()


class ReportProblemTests(CompletionTestBase):
    def test_report_blocks_customer_done_and_opens_ticket(self):
        self.worker_done()
        out = self.report()
        self.assertTrue(out["ticket_id"])
        ticket = self.conn.exec_driver_sql("SELECT booking_id, category, user_id FROM support_tickets").fetchone()
        self.assertEqual(tuple(ticket), ("b1", "SERVICE_ISSUE", "cust"))
        msg = self.conn.exec_driver_sql("SELECT content FROM ticket_messages").fetchone()
        self.assertEqual(msg[0], "Tap still leaking")
        self.assertIn((["wkr"], "completion_disputed"), self.pushes())
        with self.assertRaises(ValueError):
            self.customer_done()
        self.assertEqual(self.booking()["status"], "IN_PROGRESS")

    def test_dispute_blocks_finish_even_if_customer_confirmed_first(self):
        self.customer_done()
        self.report()
        self.assertEqual(self.worker_done()["status"], "IN_PROGRESS")

    def test_report_needs_message_and_only_once(self):
        with self.assertRaises(ValueError):
            self.report(message="  ")
        self.report()
        with self.assertRaises(ValueError):
            self.report()


class WorkerFreedTests(CompletionTestBase):
    def test_worker_free_and_not_tracked_after_done(self):
        self.assertIn("p1", _busy_provider_ids(self.conn))
        self.assertTrue(is_live_tracking(self.booking()))
        self.worker_done()
        self.assertNotIn("p1", _busy_provider_ids(self.conn))
        self.assertFalse(is_live_tracking(self.booking()))


class ReminderTests(CompletionTestBase):
    def run_at(self, minutes_after_done):
        done_at = datetime(2026, 10, 8, 10, 0, 0)
        self.set(provider_done_at=done_at)
        now = done_at.replace(tzinfo=timezone.utc) + timedelta(minutes=minutes_after_done)
        notifier = MagicMock()
        return remind_unconfirmed(self.conn, now=now, notifier=notifier), notifier

    def test_no_reminder_before_30_min(self):
        acted, notifier = self.run_at(20)
        self.assertEqual(acted, [])
        notifier.send_push.assert_not_called()

    def test_reminders_sent_once_each(self):
        self.assertEqual(self.run_at(31)[0], [("b1", "reminder_30")])
        self.assertEqual(self.run_at(45)[0], [])                 # already sent
        acted, notifier = self.run_at(125)
        self.assertEqual(acted, [("b1", "reminder_120")])
        self.assertEqual(notifier.send_push.call_args.kwargs["user_ids"], ["cust"])

    def test_flags_admin_after_24h_without_auto_completing(self):
        from unittest.mock import patch
        with patch("admin.admin_modal.AdminMaster.write_log") as log:
            acted, _ = self.run_at(24 * 60 + 5)
            self.assertEqual(acted, [("b1", "flagged")])
            self.assertEqual(log.call_args.args[2], "COMPLETION_UNCONFIRMED")
            self.assertEqual(self.run_at(48 * 60)[0], [])        # flagged once
        self.assertEqual(self.booking()["status"], "IN_PROGRESS")  # never auto-completes

    def test_no_reminder_when_disputed_or_confirmed(self):
        self.set(completion_disputed_at=datetime(2026, 10, 8, 10, 5))
        self.assertEqual(self.run_at(200)[0], [])


if __name__ == "__main__":
    unittest.main()
