import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tests.helpers import make_db, insert
from chat import messages_service as chat_mod
from utilities.contact_masking import mask_contact_info
from utilities.redis_connection import _InMemoryRedis
from utilities.time_format import to_iso_utc


class ChatTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.addCleanup(self.conn.close)
        insert(self.conn, "users", user_id="cust", phone="9876543210", role="CUSTOMER", name="Asha")
        insert(self.conn, "users", user_id="wkr", phone="9123456789", role="PROVIDER", name="Ravi")
        insert(self.conn, "users", user_id="other", phone="9111111111", role="CUSTOMER", name="X")
        insert(self.conn, "providers", provider_id="p1", user_id="wkr", status="APPROVED")
        insert(self.conn, "bookings", booking_id="b1", customer_id="cust", provider_id="p1", status="EN_ROUTE")

        self.pushed = []        # (user_id, payload)
        self.notified = []      # (user_ids, title, body, data, record_in_app)
        self.live_users = {"wkr", "cust"}

        def fake_push(conn, user_id, payload):
            self.pushed.append((str(user_id), payload))
            return 1 if str(user_id) in self.live_users else 0

        def fake_notify(_self, conn, user_ids, title, body, data=None, record_in_app=True):
            self.notified.append((user_ids, title, body, data, record_in_app))

        for target, fn in (("utilities.ws_push.push_to_user", fake_push),
                           ("notifications.notifications_service.NotificationsService.send_push", fake_notify)):
            p = patch(target, fn)
            p.start()
            self.addCleanup(p.stop)
        self.redis = _InMemoryRedis()
        p = patch("utilities.redis_connection.get_redis", return_value=self.redis)
        p.start()
        self.addCleanup(p.stop)
        self.svc = chat_mod.MessagesService()

    def send(self, user, text="hello", **extra):
        return self.svc.send_message({"_user_id": user, "_role": None, "id": "b1", "text": text, **extra}, self.conn)

    def frames(self, user, kind="chat"):
        return [p for u, p in self.pushed if u == user and p.get("message_type") == kind]


class SendTests(ChatTestBase):
    def test_frame_type_is_chat_not_overwritten(self):
        self.send("cust", client_id="c-1")
        to_worker = self.frames("wkr")
        self.assertEqual(len(to_worker), 1)
        self.assertEqual(to_worker[0]["message_type"], "chat")        # the critical bug
        self.assertEqual(to_worker[0]["content_type"], "text")
        self.assertNotIn("client_id", to_worker[0])

    def test_sender_gets_echo_with_client_id(self):
        status, out = self.send("cust", client_id="c-1")
        self.assertEqual(status, "created")
        echo = self.frames("cust")
        self.assertEqual(echo[0]["client_id"], "c-1")
        self.assertEqual(echo[0]["message_id"], out["message_id"])
        self.assertEqual(out["client_id"], "c-1")

    def test_delivered_when_recipient_online(self):
        _, out = self.send("cust")
        self.assertIsNotNone(out["delivered_at"])
        self.live_users.discard("wkr")
        _, out2 = self.send("cust")
        self.assertIsNone(out2["delivered_at"])

    def test_timestamps_are_iso_utc_with_z(self):
        _, out = self.send("cust")
        self.assertTrue(out["created_at"].endswith("Z"), out["created_at"])
        self.assertIn("T", out["created_at"])

    def test_push_notification_not_in_app(self):
        self.send("cust", text="On my way?")
        user_ids, title, body, data, record_in_app = self.notified[0]
        self.assertEqual((user_ids, title, body), (["wkr"], "Asha", "On my way?"))
        self.assertEqual(data["type"], "new_message")
        self.assertEqual(data["booking_id"], "b1")
        self.assertFalse(record_in_app)

    def test_outsider_blocked(self):
        with self.assertRaises(PermissionError):
            self.send("other")

    def test_closed_booking_blocked(self):
        for status in ("COMPLETED", "CANCELLED", "PENDING"):
            self.conn.execute(__import__("sqlalchemy").text(
                "UPDATE bookings SET status=:s WHERE booking_id='b1'"), {"s": status})
            with self.assertRaises(ValueError):
                self.send("cust")

    def test_length_and_type_validation(self):
        with self.assertRaises(ValueError):
            self.send("cust", text="x" * 1001)
        with self.assertRaises(ValueError):
            self.send("cust", message_type="image")

    def test_rate_limit(self):
        for _ in range(chat_mod.CHAT_RATE_PER_MIN):
            self.send("cust")
        with self.assertRaises(ValueError):
            self.send("cust")

    def test_contact_info_masked(self):
        _, out = self.send("cust", text="call me on +91 98765 43210")
        self.assertEqual(out["text"], "call me on 98xxxxxx10")
        self.assertTrue(out["masked"])
        self.assertNotIn("98765", self.frames("wkr")[0]["text"])


class ListAndSeenTests(ChatTestBase):
    def setUp(self):
        super().setUp()
        base = datetime(2026, 10, 5, 10, 0, 0)
        # Insert through SQLAlchemy so SQLite stores datetimes in the same
        # format the code compares against (MySQL compares real datetimes).
        from utilities.db_connection import get_table
        t = get_table("chat_messages")
        for i in range(5):
            self.conn.execute(t.insert().values(
                message_id=f"m{i}", booking_id="b1", from_id="cust", to_id="wkr",
                text=f"msg {i}", message_type="text", created_at=base + timedelta(seconds=i)))

    def listing(self, user="wkr", **params):
        return self.svc.list_messages({"_user_id": user, "_role": None, "id": "b1", **params}, self.conn)[1]

    def test_list_does_not_mark_seen_by_default(self):
        self.listing()
        self.assertEqual(self.frames("cust", "chat_seen"), [])
        self.assertTrue(all(m["seen_at"] is None for m in self.listing()))

    def test_mark_seen_param_pushes_chat_seen(self):
        self.listing(mark_seen="1")
        seen = self.frames("cust", "chat_seen")
        self.assertEqual(seen[0]["seen_by"], "wkr")
        self.assertTrue(all(m["seen_at"] for m in self.listing()))

    def test_before_pagination(self):
        page = self.listing(limit=2)
        self.assertEqual([m["message_id"] for m in page], ["m3", "m4"])
        older = self.listing(limit=2, before="m3")
        self.assertEqual([m["message_id"] for m in older], ["m1", "m2"])

    def test_since(self):
        got = self.listing(since="2026-10-05T10:00:03Z")
        self.assertEqual([m["message_id"] for m in got], ["m3", "m4"])
        with self.assertRaises(ValueError):
            self.listing(since="yesterday")

    def test_mark_seen_endpoint(self):
        status, out = self.svc.mark_seen({"_user_id": "wkr", "_role": None, "id": "b1"}, self.conn)
        self.assertEqual(out["marked"], 5)
        self.assertTrue(out["seen_at"].endswith("Z"))
        _, again = self.svc.mark_seen({"_user_id": "wkr", "_role": None, "id": "b1"}, self.conn)
        self.assertEqual(again["marked"], 0)

    def test_outsider_cannot_read(self):
        with self.assertRaises(PermissionError):
            self.listing(user="other")


class WebSocketTests(ChatTestBase):
    def setUp(self):
        super().setUp()
        insert(self.conn, "ws_connections", connection_id="conn-c", user_id="cust", booking_id="b1", role="CUSTOMER")
        self.direct = []
        p = patch("utilities.ws_push.push_to_connections",
                  lambda conn, ids, payload: self.direct.append((ids, payload)) or 1)
        p.start()
        self.addCleanup(p.stop)
        from web_sockets.web_sockets_service import WebSocketsService
        self.ws = WebSocketsService()

    def ws_send(self, **body):
        import json
        return self.ws.on_message("conn-c", {"body": json.dumps({"booking_id": "b1", **body})}, self.conn)

    def test_ws_send_success_echoes_client_id(self):
        status, _ = self.ws_send(text="hi", client_id="c-9")
        self.assertEqual(status, "created")
        self.assertEqual(self.frames("cust")[0]["client_id"], "c-9")

    def test_ws_send_error_sends_chat_error(self):
        status, reason = self.ws_send(text="", client_id="c-10")
        self.assertEqual(status, "error")
        ids, payload = self.direct[0]
        self.assertEqual(ids, ["conn-c"])
        self.assertEqual(payload["message_type"], "chat_error")
        self.assertEqual(payload["client_id"], "c-10")
        self.assertEqual(payload["error"], "Message can't be empty")

    def test_ws_mark_seen(self):
        import json
        status, out = self.ws.on_mark_seen("conn-c", {"body": json.dumps({"booking_id": "b1"})}, self.conn)
        self.assertEqual(status, "success")


class MaskingTests(unittest.TestCase):
    def test_variants(self):
        cases = {
            "9876543210": "98xxxxxx10",
            "my no is 98765-43210 ok": "my no is 98xxxxxx10 ok",
            "+919876543210": "98xxxxxx10",
            "09876543210": "98xxxxxx10",
            "mail a@b.com": "mail [email hidden]",
            "pay to ravi@okicici": "pay to [UPI hidden]",
        }
        for raw, expected in cases.items():
            self.assertEqual(mask_contact_info(raw), (expected, True), raw)

    def test_normal_text_untouched(self):
        for txt in ("Reached in 10 mins", "Flat 402, 2nd floor", "Booking 12345", "OTP is 4821"):
            self.assertEqual(mask_contact_info(txt), (txt, False), txt)

    def test_iso(self):
        self.assertEqual(to_iso_utc(datetime(2026, 10, 5, 10, 0, 0)), "2026-10-05T10:00:00.000Z")


if __name__ == "__main__":
    unittest.main()
