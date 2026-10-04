import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.helpers import make_db, insert

from bookings.live_tracking import (
    is_live_tracking, live_tracking_bookings, check_arrival, check_late, nudge_stale_trackers,
)
from utilities.db_connection import get_table
from web_sockets.web_sockets_service import _provider_assigned

NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


class IsLiveTrackingTests(unittest.TestCase):
    def test_en_route_and_in_progress_always_live(self):
        for status in ("EN_ROUTE", "IN_PROGRESS"):
            self.assertTrue(is_live_tracking({"status": status, "scheduled_at": NOW + timedelta(days=2)}, NOW))

    def test_accepted_only_within_pre_job_window(self):
        self.assertTrue(is_live_tracking({"status": "ACCEPTED", "scheduled_at": NOW + timedelta(minutes=45)}, NOW))
        self.assertFalse(is_live_tracking({"status": "ACCEPTED", "scheduled_at": NOW + timedelta(hours=5)}, NOW))

    def test_naive_mysql_datetime_treated_as_utc(self):
        naive = (NOW + timedelta(minutes=30)).replace(tzinfo=None)
        self.assertTrue(is_live_tracking({"status": "ACCEPTED", "scheduled_at": naive}, NOW))

    def test_finished_bookings_never_live(self):
        for status in ("PENDING", "COMPLETED", "CANCELLED"):
            self.assertFalse(is_live_tracking({"status": status, "scheduled_at": NOW}, NOW))


class LiveTrackingQueryTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        insert(self.conn, "bookings", booking_id="live", provider_id="p1", customer_id="c1",
               status="EN_ROUTE", scheduled_at=now)
        insert(self.conn, "bookings", booking_id="soon", provider_id="p1", customer_id="c2",
               status="ACCEPTED", scheduled_at=now + timedelta(minutes=30))
        insert(self.conn, "bookings", booking_id="tomorrow", provider_id="p1", customer_id="c3",
               status="ACCEPTED", scheduled_at=now + timedelta(days=1))
        insert(self.conn, "bookings", booking_id="done", provider_id="p1", customer_id="c4",
               status="COMPLETED", scheduled_at=now - timedelta(hours=3))

    def tearDown(self):
        self.conn.close()

    def test_broadcast_skips_future_and_finished_bookings(self):
        ids = {r.booking_id for r in live_tracking_bookings(self.conn, "p1")}
        # "soon" is in its window but the expert is EN_ROUTE on "live" (see BackToBackTests).
        self.assertEqual(ids, {"live"})
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "live").values(status="COMPLETED"))
        ids = {r.booking_id for r in live_tracking_bookings(self.conn, "p1")}
        self.assertEqual(ids, {"soon"})

    def test_ws_location_only_for_live_booking(self):
        self.assertTrue(_provider_assigned(self.conn, "live", "p1"))
        self.assertFalse(_provider_assigned(self.conn, "tomorrow", "p1"))
        self.assertFalse(_provider_assigned(self.conn, "done", "p1"))
        self.assertFalse(_provider_assigned(self.conn, "live", "p2"))


HOME = {"lat": 17.7200, "lng": 83.3000}


class ArrivalAlertTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        insert(self.conn, "bookings", booking_id="b1", provider_id="p1", customer_id="c1",
               status="EN_ROUTE", address_snapshot=json.dumps(HOME))
        self.notifier = mock.Mock()

    def tearDown(self):
        self.conn.close()

    def _row(self):
        b = get_table("bookings")
        return self.conn.execute(b.select().where(b.c.booking_id == "b1")).fetchone()

    def _ping(self, lat, lng, eta=None):
        return check_arrival(self.conn, self._row(), lat, lng, eta, notifier=self.notifier)

    def test_far_away_sends_nothing(self):
        self.assertIsNone(self._ping(17.80, 83.40, {"duration_min": 15}))
        self.notifier.send_push.assert_not_called()

    def test_arriving_fires_once_by_eta(self):
        self.assertEqual(self._ping(17.735, 83.315, {"duration_min": 2}), "arriving")
        self.assertIsNone(self._ping(17.734, 83.314, {"duration_min": 1}))
        self.assertEqual(self.notifier.send_push.call_count, 1)

    def test_arrived_fires_once_and_suppresses_arriving(self):
        self.assertEqual(self._ping(17.7203, 83.3002), "arrived")
        self.assertIsNone(self._ping(17.7201, 83.3001))
        self.assertIsNone(self._ping(17.7230, 83.3020))  # stepped back out: no late "arriving"
        self.assertEqual(self.notifier.send_push.call_count, 1)
        self.assertEqual(self.notifier.send_push.call_args.kwargs["user_ids"], ["c1"])

    def test_only_en_route(self):
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "b1").values(status="IN_PROGRESS"))
        self.assertIsNone(self._ping(17.7200, 83.3000))


def _utc_naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class BackToBackTests(unittest.TestCase):
    """One expert, two customers: B must not watch the expert inside A's home."""

    def setUp(self):
        self.conn = make_db()
        now = datetime.now(timezone.utc)
        insert(self.conn, "bookings", booking_id="A", provider_id="p1", customer_id="c1",
               status="IN_PROGRESS", scheduled_at=_utc_naive(now - timedelta(minutes=40)))
        insert(self.conn, "bookings", booking_id="B", provider_id="p1", customer_id="c2",
               status="ACCEPTED", scheduled_at=_utc_naive(now + timedelta(minutes=30)))

    def tearDown(self):
        self.conn.close()

    def _row(self, bid):
        b = get_table("bookings")
        return self.conn.execute(b.select().where(b.c.booking_id == bid)).fetchone()

    def test_broadcast_only_to_current_job(self):
        self.assertEqual({r.booking_id for r in live_tracking_bookings(self.conn, "p1")}, {"A"})

    def test_next_customer_blocked_while_busy(self):
        self.assertFalse(is_live_tracking(self._row("B"), conn=self.conn))
        self.assertFalse(_provider_assigned(self.conn, "B", "p1"))
        self.assertTrue(is_live_tracking(self._row("A"), conn=self.conn))

    def test_next_customer_live_once_previous_job_done(self):
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "A").values(status="COMPLETED"))
        self.assertTrue(is_live_tracking(self._row("B"), conn=self.conn))


class RunningLateTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.slot = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)  # 15:30 IST
        insert(self.conn, "bookings", booking_id="b1", provider_id="p1", customer_id="c1",
               status="EN_ROUTE", scheduled_at=_utc_naive(self.slot), address_snapshot=json.dumps(HOME))
        self.notifier = mock.Mock()

    def tearDown(self):
        self.conn.close()

    def _late(self, minutes, now_offset=0):
        b = get_table("bookings")
        row = self.conn.execute(b.select().where(b.c.booking_id == "b1")).fetchone()
        return check_late(self.conn, row, {"duration_min": minutes},
                          now=self.slot + timedelta(minutes=now_offset), notifier=self.notifier)

    def test_on_time_sends_nothing(self):
        self.assertFalse(self._late(8))
        self.notifier.send_push.assert_not_called()

    def test_late_fires_once_with_ist_time(self):
        self.assertTrue(self._late(25))
        self.assertFalse(self._late(30, now_offset=2))
        self.assertEqual(self.notifier.send_push.call_count, 1)
        self.assertIn("3:55 PM", self.notifier.send_push.call_args.kwargs["body"])

    def test_no_late_alert_after_arriving_alert(self):
        b = get_table("bookings")
        self.conn.execute(b.update().where(b.c.booking_id == "b1")
                          .values(service_snapshot=json.dumps({"arriving_notified": True})))
        self.assertFalse(self._late(25))


class StaleNudgeTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.now = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        insert(self.conn, "providers", provider_id="p1", user_id="u1")
        insert(self.conn, "bookings", booking_id="b1", provider_id="p1", customer_id="c1", status="EN_ROUTE")
        self.notifier = mock.Mock()

    def tearDown(self):
        self.conn.close()

    def _loc(self, minutes_ago):
        insert(self.conn, "provider_locations", provider_id="p1", lat=17.7, lng=83.3,
               updated_at=_utc_naive(self.now - timedelta(minutes=minutes_ago)))

    def test_fresh_location_not_nudged(self):
        self._loc(2)
        self.assertEqual(nudge_stale_trackers(self.conn, self.now, self.notifier), [])

    def test_stale_location_nudges_worker_once(self):
        self._loc(20)
        self.assertEqual(nudge_stale_trackers(self.conn, self.now, self.notifier), ["b1"])
        self.assertEqual(nudge_stale_trackers(self.conn, self.now, self.notifier), [])
        self.assertEqual(self.notifier.send_push.call_args.kwargs["user_ids"], ["u1"])

    def test_never_shared_location_is_nudged(self):
        self.assertEqual(nudge_stale_trackers(self.conn, self.now, self.notifier), ["b1"])


if __name__ == "__main__":
    unittest.main()
