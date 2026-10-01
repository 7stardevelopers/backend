import os
import unittest
from unittest.mock import patch, MagicMock

import requests

from tests.helpers import make_db, insert
from calls import calls_service as svc_mod
from calls import exotel_client
from utilities.redis_connection import _InMemoryRedis

EXOTEL_ENV = {
    "EXOTEL_SID": "acct_sid", "EXOTEL_API_KEY": "KEY123", "EXOTEL_API_TOKEN": "TOKEN456",
    "EXOTEL_SUBDOMAIN": "api.exotel.com", "EXOPHONE": "07940000000",
    "EXOTEL_CALLBACK_SECRET": "cb-secret", "EXOTEL_STATUS_CALLBACK_URL": "https://x/calls/status-callback?token=cb-secret",
}


def _ok_response(sid="CA123"):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"Call": {"Sid": sid}}
    return resp


class CallsTestBase(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, EXOTEL_ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.conn = make_db()
        self.addCleanup(self.conn.close)
        insert(self.conn, "users", user_id="cust", phone="9876543210", role="CUSTOMER")
        insert(self.conn, "users", user_id="wkr", phone="+91 91234 56789", role="PROVIDER")
        insert(self.conn, "users", user_id="adm", phone="9000000000", role="ADMIN")
        insert(self.conn, "users", user_id="other", phone="9111111111", role="CUSTOMER")
        insert(self.conn, "providers", provider_id="p1", user_id="wkr", status="APPROVED")
        insert(self.conn, "bookings", booking_id="b1", customer_id="cust", provider_id="p1", status="EN_ROUTE")
        self.redis = _InMemoryRedis()
        p = patch.object(svc_mod, "get_redis", return_value=self.redis)
        p.start()
        self.addCleanup(p.stop)
        self.svc = svc_mod.CallsService()
        # failed attempts are written on a separate committed connection in prod;
        # in tests write them to the shared in-memory DB
        self.svc.modal.create_committed = lambda data: self.svc.modal.create(self.conn, data)

    def call(self, user, role, **body):
        return self.svc.initiate_call({"_user_id": user, "_role": role, **body}, self.conn)

    def logs(self):
        from sqlalchemy import text
        return [dict(r) for r in self.conn.execute(text("SELECT * FROM call_logs ORDER BY created_at")).mappings()]


class InitiateCallTests(CallsTestBase):
    @patch("calls.exotel_client.requests.post")
    def test_customer_calls_worker_masked(self, post):
        post.return_value = _ok_response()
        status, data = self.call("cust", "CUSTOMER", booking_id="b1", target="provider")
        self.assertEqual(status, "success")
        self.assertNotIn("9876543210", str(data))          # no numbers in the response
        self.assertNotIn("91234", str(data))
        args, kwargs = post.call_args
        self.assertEqual(kwargs["data"]["From"], "09876543210")   # caller rings first
        self.assertEqual(kwargs["data"]["To"], "09123456789")     # normalised +91 number
        self.assertEqual(kwargs["data"]["CallerId"], "07940000000")
        self.assertEqual(kwargs["auth"], ("KEY123", "TOKEN456"))
        self.assertNotIn("KEY123", args[0])                     # credentials never in URL
        self.assertEqual(self.logs()[0]["status"], "INITIATED")

    @patch("calls.exotel_client.requests.post")
    def test_worker_calls_customer(self, post):
        post.return_value = _ok_response()
        self.call("wkr", "PROVIDER", booking_id="b1", target="customer")
        self.assertEqual(post.call_args.kwargs["data"]["From"], "09123456789")

    def test_other_customer_blocked(self):
        with self.assertRaises(PermissionError):
            self.call("other", "CUSTOMER", booking_id="b1", target="provider")

    def test_customer_cannot_target_customer(self):
        with self.assertRaises(PermissionError):
            self.call("cust", "CUSTOMER", booking_id="b1", target="customer")

    def test_inactive_booking_blocked(self):
        insert(self.conn, "bookings", booking_id="b2", customer_id="cust", provider_id="p1", status="COMPLETED")
        with self.assertRaises(ValueError):
            self.call("cust", "CUSTOMER", booking_id="b2", target="provider")

    @patch("calls.exotel_client.requests.post")
    def test_exotel_error_is_friendly_and_logged(self, post):
        resp = MagicMock(status_code=403)
        resp.json.return_value = {"RestException": {"Status": 403, "Message": "Authentication failed"}}
        post.return_value = resp
        with self.assertRaises(ValueError) as ctx:
            self.call("cust", "CUSTOMER", booking_id="b1", target="provider")
        self.assertEqual(str(ctx.exception), svc_mod.GENERIC_FAILURE)
        log = self.logs()[0]
        self.assertEqual(log["status"], "FAILED")
        self.assertIn("Authentication failed", log["error_message"])
        self.assertNotIn("TOKEN456", log["error_message"])

    @patch("calls.exotel_client.requests.post")
    def test_timeout_never_leaks_credentials(self, post):
        post.side_effect = requests.ConnectionError("https://KEY123:TOKEN456@api.exotel.com failed")
        with self.assertRaises(ValueError):
            self.call("cust", "CUSTOMER", booking_id="b1", target="provider")
        self.assertNotIn("TOKEN456", self.logs()[0]["error_message"])

    def test_not_configured(self):
        with patch.dict(os.environ, {"EXOPHONE": ""}):
            with self.assertRaises(ValueError) as ctx:
                self.call("cust", "CUSTOMER", booking_id="b1", target="provider")
        self.assertIn("not available", str(ctx.exception))

    @patch("calls.exotel_client.requests.post")
    def test_cooldown_and_window_limit(self, post):
        post.return_value = _ok_response()
        self.call("cust", "CUSTOMER", booking_id="b1", target="provider")
        with self.assertRaises(ValueError):   # immediate second tap
            self.call("cust", "CUSTOMER", booking_id="b1", target="provider")
        for _ in range(svc_mod.CALL_WINDOW_MAX - 1):
            self.redis.delete("call_cd:cust:b1")
            self.call("cust", "CUSTOMER", booking_id="b1", target="provider")
        self.redis.delete("call_cd:cust:b1")
        with self.assertRaises(ValueError):
            self.call("cust", "CUSTOMER", booking_id="b1", target="provider")

    @patch("calls.exotel_client.requests.post")
    def test_admin_phone_rings_first(self, post):
        post.return_value = _ok_response()
        self.call("adm", "ADMIN", target_user_id="cust")
        self.assertEqual(post.call_args.kwargs["data"]["From"], "09000000000")
        self.assertEqual(post.call_args.kwargs["data"]["To"], "09876543210")

    def test_direct_call_requires_admin(self):
        with self.assertRaises(PermissionError):
            self.call("cust", "CUSTOMER", target_user_id="wkr")


class CallbackTests(CallsTestBase):
    def setUp(self):
        super().setUp()
        insert(self.conn, "call_logs", call_id="c1", booking_id="b1", initiated_by="cust",
               target="PROVIDER", exotel_call_sid="CA999", status="INITIATED")

    def cb(self, **body):
        return self.svc.status_callback({"_user_id": None, "_role": None, **body}, self.conn)

    def test_valid_callback_records_details(self):
        self.cb(token="cb-secret", CallSid="CA999", Status="completed",
                ConversationDuration="42", StartTime="2026-10-01 10:00:00",
                RecordingUrl="https://rec/1.mp3")
        log = self.logs()[0]
        self.assertEqual((log["status"], log["duration_sec"]), ("COMPLETED", 42))
        self.assertEqual(str(log["start_time"])[:19], "2026-10-01 04:30:00")   # IST → UTC
        self.assertEqual(log["recording_url"], "https://rec/1.mp3")

    def test_wrong_token_rejected(self):
        with self.assertRaises(PermissionError):
            self.cb(token="nope", CallSid="CA999", Status="completed")

    def test_missing_secret_rejected_when_deployed(self):
        with patch.dict(os.environ, {"EXOTEL_CALLBACK_SECRET": "", "ENVIRONMENT": "staging"}):
            with self.assertRaises(PermissionError):
                self.cb(CallSid="CA999", Status="completed")

    def test_status_from_legs(self):
        self.cb(token="cb-secret", CallSid="CA999", Legs=[{"Status": "completed"}, {"Status": "no-answer"}])
        self.assertEqual(self.logs()[0]["status"], "NO-ANSWER")

    def test_unknown_sid_is_ok(self):
        status, _ = self.cb(token="cb-secret", CallSid="UNKNOWN", Status="busy")
        self.assertEqual(status, "success")


class ExotelFormCallbackParsingTests(unittest.TestCase):
    def test_form_encoded_callback_parses(self):
        from request_handler import parse_request
        event = {
            "httpMethod": "POST", "path": "/calls/status-callback",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "queryStringParameters": {"token": "cb-secret"},
            "body": "CallSid=CA1&Status=busy&ConversationDuration=0",
        }
        body = parse_request(event)["body"]
        self.assertEqual((body["CallSid"], body["Status"], body["token"]), ("CA1", "busy", "cb-secret"))

    def test_number_normalisation(self):
        self.assertEqual(exotel_client.normalize_number("+91 98765-43210"), "09876543210")
        self.assertEqual(exotel_client.normalize_number("09876543210"), "09876543210")
        with self.assertRaises(ValueError):
            exotel_client.normalize_number("12345")


if __name__ == "__main__":
    unittest.main()
