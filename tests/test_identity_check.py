"""The door OTP exists only after the customer confirms the worker's face at the door."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from tests.helpers import make_db, insert
from bookings.bookings_modal import BookingsMaster
from bookings.bookings_service import BookingsService
from identity_reports.identity_reports_service import IdentityReportsService


class IdentityCheckTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.addCleanup(self.conn.close)
        push = patch("notifications.notifications_service.NotificationsService.send_push")
        push.start()
        self.addCleanup(push.stop)
        insert(self.conn, "users", user_id="cust", role="CUSTOMER", name="Asha")
        insert(self.conn, "users", user_id="wkr", role="PROVIDER", name="Ravi")
        insert(self.conn, "users", user_id="adm", role="ADMIN", name="Admin")
        insert(self.conn, "providers", provider_id="p1", user_id="wkr", status="APPROVED")
        insert(self.conn, "bookings", booking_id="b1", customer_id="cust", provider_id="p1",
               status="EN_ROUTE", service_snapshot="{}", created_at=datetime.utcnow() - timedelta(days=7))
        self.svc = IdentityReportsService()
        self.svc.booking_svc.notif = MagicMock()
        self.books = BookingsService()
        self.books.notif = MagicMock()

    def check(self, match, user="cust", role="CUSTOMER", **extra):
        return self.svc.door_check({"_user_id": user, "_role": role, "id": "b1", "match": match, **extra}, self.conn)[1]

    def booking(self):
        return BookingsMaster().read_one(self.conn, "b1")

    def detail(self, user, role):
        # What GET /bookings/{id} hands each role (the full detail needs tables the fixture lacks).
        return BookingsService._hide_door_otp(self.booking(), role)

    def verify(self, otp):
        return self.books.verify_door_otp({"_user_id": "wkr", "_role": "PROVIDER", "id": "b1", "otp": otp}, self.conn)

    def test_new_booking_has_no_door_otp(self):
        b = BookingsMaster().create(self.conn, {"customer_id": "cust", "status": "PENDING"})
        self.assertIsNone(b.get("door_otp"))

    def test_worker_cannot_start_before_customer_confirms(self):
        with self.assertRaisesRegex(ValueError, "hasn't confirmed"):
            self.verify("1234")

    def test_yes_issues_otp_that_starts_the_job(self):
        res = self.check(True)
        self.assertEqual(len(res["door_otp"]), 4)
        self.assertEqual(self.detail("cust", "CUSTOMER")["door_otp"], res["door_otp"])
        self.assertNotIn("door_otp", self.detail("wkr", "PROVIDER"))
        self.verify(res["door_otp"])
        self.assertEqual(self.booking()["status"], "IN_PROGRESS")

    def test_yes_twice_returns_same_code(self):
        self.assertEqual(self.check(True)["door_otp"], self.check(True)["door_otp"])

    def test_customer_never_sees_otp_before_confirming(self):
        # e.g. a booking from before this change that already had a code
        BookingsMaster().regenerate_door_otp(self.conn, "b1")
        self.assertNotIn("door_otp", self.detail("cust", "CUSTOMER"))
        listed = self.books.list_mine({"_user_id": "cust", "_role": "CUSTOMER"}, self.conn)[1]
        self.assertNotIn("door_otp", listed[0])
        self.assertIn("door_otp", self.detail("adm", "ADMIN"))

    def test_no_reports_to_admin_and_withdraws_otp(self):
        code = self.check(True)["door_otp"]
        res = self.check(False, note="Different man, older")
        self.assertFalse(res["match"])
        b = self.booking()
        self.assertIsNone(b["door_otp"])
        self.assertIsNotNone(b["identity_mismatch_at"])
        with self.assertRaises(ValueError):
            self.verify(code)
        listed = self.svc.admin_list({"_user_id": "adm", "_role": "ADMIN"}, self.conn)[1]
        self.assertEqual(listed["open_count"], 1)
        item = listed["items"][0]
        self.assertEqual((item["provider_name"], item["customer_note"]), ("Ravi", "Different man, older"))
        ticket = self.conn.exec_driver_sql("SELECT category, priority FROM support_tickets").fetchone()
        self.assertEqual(tuple(ticket), ("SAFETY", "URGENT"))

    def test_second_no_does_not_duplicate_report(self):
        first = self.check(False)["report_id"]
        self.assertEqual(self.check(False)["report_id"], first)

    def test_only_the_bookings_customer(self):
        insert(self.conn, "users", user_id="other", role="CUSTOMER")
        with self.assertRaises(PermissionError):
            self.check(True, user="other")
        with self.assertRaises(PermissionError):
            self.check(True, user="wkr", role="PROVIDER")

    def test_not_after_job_started(self):
        self.verify(self.check(True)["door_otp"])
        with self.assertRaises(ValueError):
            self.check(True)

    def test_resend_needs_confirmation(self):
        with self.assertRaises(ValueError):
            self.books.regenerate_door_otp({"_user_id": "cust", "_role": "CUSTOMER", "id": "b1"}, self.conn)

    def test_admin_resolves_report(self):
        rid = self.check(False)["report_id"]
        out = self.svc.admin_resolve({"_user_id": "adm", "_role": "ADMIN", "id": rid,
                                      "status": "ACTION_TAKEN", "admin_note": "Worker suspended"}, self.conn)[1]
        self.assertEqual((out["status"], out["resolved_by"]), ("ACTION_TAKEN", "adm"))
        with self.assertRaises(PermissionError):
            self.svc.admin_resolve({"_user_id": "cust", "_role": "CUSTOMER", "id": rid, "status": "DISMISSED"}, self.conn)


class CustomerListPhotoTests(unittest.TestCase):
    def test_customer_list_carries_expert_photo(self):
        conn = make_db()
        self.addCleanup(conn.close)
        insert(conn, "users", user_id="cust", role="CUSTOMER")
        insert(conn, "users", user_id="wkr", role="PROVIDER", name="Ravi", photo_url="https://x/profile/wkr/a.jpg")
        insert(conn, "providers", provider_id="p1", user_id="wkr", avg_rating=4.6)
        insert(conn, "bookings", booking_id="b1", customer_id="cust", provider_id="p1", status="ACCEPTED")
        insert(conn, "bookings", booking_id="b2", customer_id="cust", status="PENDING")
        svc = BookingsService()
        rows = {b["booking_id"]: b for b in svc.list_mine({"_user_id": "cust", "_role": "CUSTOMER"}, conn)[1]}
        self.assertEqual(rows["b1"]["provider_photo"], "https://x/profile/wkr/a.jpg")
        self.assertEqual(rows["b1"]["provider_name"], "Ravi")
        self.assertNotIn("provider_photo", rows["b2"])


if __name__ == "__main__":
    unittest.main()
