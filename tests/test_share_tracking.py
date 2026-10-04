import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests.helpers import make_db, insert

from bookings.share_tracking import ShareTrackingService
from utilities.auth_tokens import issue_track_token, decode_track_token, decode_access_token
from utilities.db_connection import get_table

HOME = {"lat": 17.72, "lng": 83.30, "full_address": "Flat 4B, Secret Street"}
ETA = {"distance_km": 2.4, "duration_min": 9, "source": "google"}


class ShareTrackingTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        insert(self.conn, "users", user_id="c1", name="Asha Rao", role="CUSTOMER")
        insert(self.conn, "users", user_id="u1", name="Ravi Kumar", phone="9999999999", role="PROVIDER")
        insert(self.conn, "providers", provider_id="p1", user_id="u1", avg_rating=4.76)
        insert(self.conn, "services", service_id="s1", name="AC Repair", base_price=49900)
        insert(self.conn, "bookings", booking_id="b1", provider_id="p1", customer_id="c1", service_id="s1",
               status="EN_ROUTE", address_snapshot=json.dumps(HOME))
        insert(self.conn, "provider_locations", provider_id="p1", lat=17.73, lng=83.31,
               updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
        self.svc = ShareTrackingService()
        patcher = mock.patch("providers.providers_service._cached_road_eta", return_value=ETA)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.conn.close()

    def _snapshot(self, token=None):
        _, data = self.svc.public_snapshot({"token": token or issue_track_token("b1")}, self.conn)
        return data

    def test_link_is_customer_only(self):
        _, data = self.svc.create_link({"_user_id": "c1", "_role": "CUSTOMER", "id": "b1"}, self.conn)
        self.assertEqual(decode_track_token(data["token"]), "b1")
        self.assertTrue(data["path"].endswith("/page"))
        with self.assertRaises(PermissionError):
            self.svc.create_link({"_user_id": "u1", "_role": "PROVIDER", "id": "b1"}, self.conn)

    def test_track_token_cannot_call_the_api(self):
        with self.assertRaises(PermissionError):
            decode_access_token(issue_track_token("b1"))

    def test_live_snapshot_has_location_but_no_private_details(self):
        d = self._snapshot()
        self.assertEqual(d["location"]["lat"], 17.73)
        self.assertEqual(d["eta"], ETA)
        self.assertEqual(d["expert"], {"first_name": "Ravi", "rating": 4.8})
        self.assertEqual(d["service_name"], "AC Repair")
        dumped = json.dumps(d, default=str)
        self.assertNotIn("9999999999", dumped)
        self.assertNotIn("Secret Street", dumped)

    def test_finished_booking_hides_location(self):
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "b1").values(status="COMPLETED"))
        d = self._snapshot()
        self.assertFalse(d["live"])
        self.assertIsNone(d["location"])
        self.assertIsNone(d["destination"])

    def test_expired_link(self):
        expired = issue_track_token("b1", hours=-1)
        with self.assertRaises(PermissionError) as ctx:
            self._snapshot(expired)
        self.assertEqual(str(ctx.exception), "Link expired")
        status, page = self.svc.public_page({"token": expired}, self.conn)
        self.assertEqual(status, "html")
        self.assertIn('LINK_ERROR = "Link expired"', page)


if __name__ == "__main__":
    unittest.main()
