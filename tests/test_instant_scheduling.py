"""Instant ("Now") and scheduled bookings: slot rules, dispatch rounds, expiry, clashes."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.helpers import make_db, insert
from bookings import dispatch, dispatch_rules as rules
from bookings.bookings_service import BookingsService, cancellation_fee_for
from bookings.bookings_validator import CreateBookingSchema
from instant_bookings.instant_bookings_service import InstantBookingsService

IST = rules.IST
HOME = {"full_address": "12 MG Road", "lat": 17.4000, "lng": 78.4000}


def ist(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


class SlotRuleTests(unittest.TestCase):
    NOW = ist(2026, 10, 14, 10, 0)  # 10:00 IST

    def test_valid_slot(self):
        rules.validate_slot(ist(2026, 10, 14, 11, 30), self.NOW)

    def test_too_soon(self):
        with self.assertRaisesRegex(ValueError, "at least 60 minutes"):
            rules.validate_slot(ist(2026, 10, 14, 10, 30), self.NOW)

    def test_outside_hours(self):
        for when in (ist(2026, 10, 15, 6, 30), ist(2026, 10, 15, 21, 0), ist(2026, 10, 15, 23, 0)):
            with self.assertRaisesRegex(ValueError, "between"):
                rules.validate_slot(when, self.NOW)

    def test_too_far_ahead(self):
        with self.assertRaisesRegex(ValueError, "14 days"):
            rules.validate_slot(ist(2026, 10, 29, 12, 0), self.NOW)

    def test_slots_for_today_mark_past_ones(self):
        slots = rules.slots_for(self.NOW.date(), self.NOW)
        self.assertEqual(len(slots), 28)  # 07:00 … 20:30 every 30 min
        by_time = {s["time"]: s for s in slots}
        self.assertFalse(by_time["10:30"]["available"])
        self.assertEqual(by_time["10:30"]["reason"], "too_soon")
        self.assertTrue(by_time["11:00"]["available"])
        self.assertTrue(by_time["11:00"]["scheduled_at"].endswith("Z"))

    def test_instant_open_hours(self):
        self.assertTrue(rules.instant_open(ist(2026, 10, 14, 10, 0)))
        self.assertFalse(rules.instant_open(ist(2026, 10, 14, 20, 30)))  # can't arrive before 21:00
        self.assertFalse(rules.instant_open(ist(2026, 10, 14, 6, 0)))

    def test_instant_radius_widens(self):
        start = self.NOW
        self.assertEqual(rules.instant_radius_due(start, start + timedelta(minutes=1)), 5)
        self.assertEqual(rules.instant_radius_due(start, start + timedelta(minutes=3)), 8)
        self.assertEqual(rules.instant_radius_due(start, start + timedelta(minutes=6)), 12)

    def test_scheduled_reoffer_marks(self):
        slot = ist(2026, 10, 16, 12, 0)
        booked = ist(2026, 10, 14, 10, 0)
        self.assertIsNone(rules.scheduled_round_due(slot, booked, booked + timedelta(hours=1)))
        self.assertEqual(rules.scheduled_round_due(slot, booked, slot - timedelta(hours=23)), 24)
        self.assertEqual(rules.scheduled_round_due(slot, slot - timedelta(hours=23), slot - timedelta(minutes=50)), 1)

    def test_schema_types(self):
        legacy = CreateBookingSchema(service_id="s1", is_instant=True)
        self.assertEqual((legacy.booking_type, legacy.is_instant, legacy.scheduled_at), ("INSTANT", True, None))
        with self.assertRaisesRegex(ValueError, "scheduled_at is required"):
            CreateBookingSchema(service_id="s1")
        with self.assertRaisesRegex(ValueError, "future"):
            CreateBookingSchema(service_id="s1", scheduled_at=datetime.utcnow() - timedelta(hours=1))
        instant = CreateBookingSchema(service_id="s1", booking_type="INSTANT",
                                      scheduled_at=datetime.utcnow() - timedelta(hours=1))
        self.assertIsNone(instant.scheduled_at)  # ignored for instant

    def test_clash_window_counts_travel(self):
        a = ist(2026, 10, 16, 12, 0)
        self.assertTrue(rules.windows_clash(a, a + timedelta(hours=1),
                                            a + timedelta(minutes=80), a + timedelta(minutes=140)))
        self.assertFalse(rules.windows_clash(a, a + timedelta(hours=1),
                                             a + timedelta(minutes=95), a + timedelta(minutes=155)))


class DispatchTestBase(unittest.TestCase):
    def setUp(self):
        push = patch("notifications.notifications_service.NotificationsService.send_push")
        self.push = push.start()
        self.addCleanup(push.stop)
        silent = patch("notifications.notifications_service.NotificationsService.send_silent")
        self.silent = silent.start()
        self.addCleanup(silent.stop)
        opened = patch("bookings.dispatch_rules.instant_open", return_value=True)
        opened.start()
        self.addCleanup(opened.stop)

        self.conn = make_db()
        self.addCleanup(self.conn.close)
        insert(self.conn, "users", user_id="cust", role="CUSTOMER", name="Asha")
        insert(self.conn, "services", service_id="s1", name="Plumbing", base_price=19900,
               is_active=True, instant_available=True)
        insert(self.conn, "services", service_id="s2", name="Painting", base_price=79900,
               is_active=True, instant_available=False)
        # near: ~2 km, mid: ~7 km, far: ~30 km from HOME
        for pid, uid, dlat in (("p_near", "w_near", 0.018), ("p_mid", "w_mid", 0.063), ("p_far", "w_far", 0.27)):
            insert(self.conn, "users", user_id=uid, role="PROVIDER", name=uid)
            insert(self.conn, "providers", provider_id=pid, user_id=uid, status="APPROVED", is_available=True)
            insert(self.conn, "provider_services", provider_id=pid, service_id="s1")
            insert(self.conn, "provider_locations", provider_id=pid, lat=HOME["lat"] + dlat, lng=HOME["lng"])
        self.books = BookingsService()

    def create(self, **body):
        body = {"_user_id": "cust", "_role": "CUSTOMER", "service_id": "s1",
                "address_snapshot": dict(HOME), **body}
        return self.books.create(body, self.conn)[1]

    def booking(self, booking_id):
        return self.books.modal.read_one(self.conn, booking_id)

    def offers(self, push_type):
        """User ids offered a job via pushes of push_type, in order."""
        out = []
        for call in self.push.call_args_list:
            if call.kwargs.get("data", {}).get("type") == push_type:
                out.extend(call.kwargs["user_ids"])
        return out

    def set(self, booking_id, **values):
        sets = ", ".join(f"{k}=?" for k in values)
        self.conn.exec_driver_sql(f"UPDATE bookings SET {sets} WHERE booking_id=?",
                                  (*values.values(), booking_id))

    def accept(self, booking_id, uid):
        return self.books.accept_booking({"_user_id": uid, "_role": "PROVIDER", "id": booking_id}, self.conn)[1]


class InstantBookingTests(DispatchTestBase):
    def test_instant_offered_nearby_first_and_nobody_assigned(self):
        b = self.create(booking_type="INSTANT")
        row = self.booking(b["booking_id"])
        self.assertEqual((row["status"], row["provider_id"]), ("PENDING", None))
        self.assertTrue(row["is_instant"])
        self.assertEqual((row["dispatch_attempts"], row["dispatch_radius_km"]), (1, 5))
        self.assertIsNotNone(row["expires_at"])
        self.assertEqual(self.offers("instant_job"), ["w_near"])
        ib = self.conn.exec_driver_sql("SELECT status, dispatched_at FROM instant_bookings").fetchone()
        self.assertEqual(ib[0], "DISPATCHING")
        self.assertIsNotNone(ib[1])

    def test_radius_widens_to_new_ring_only_once(self):
        b = self.create(booking_type="INSTANT")
        later = datetime.now(timezone.utc) + timedelta(minutes=3)
        dispatch.tick_one(self.conn, self.booking(b["booking_id"]), now=later)
        dispatch.tick_one(self.conn, self.booking(b["booking_id"]), now=later)  # second tick: no repeat
        self.assertEqual(self.offers("instant_job"), ["w_near", "w_mid"])
        self.assertEqual(self.booking(b["booking_id"])["dispatch_radius_km"], 8)

    def test_expires_when_nobody_accepts(self):
        b = self.create(booking_type="INSTANT")
        dispatch.tick_one(self.conn, self.booking(b["booking_id"]),
                          now=datetime.now(timezone.utc) + timedelta(minutes=11))
        row = self.booking(b["booking_id"])
        self.assertEqual((row["status"], row["cancellation_reason"]), ("CANCELLED", "NO_EXPERT"))
        self.assertEqual(self.offers("booking_expired"), ["cust"])
        ib = self.conn.exec_driver_sql("SELECT status, expired_at FROM instant_bookings").fetchone()
        self.assertEqual(ib[0], "EXPIRED")
        self.assertIsNotNone(ib[1])

    def test_cannot_accept_after_deadline(self):
        b = self.create(booking_type="INSTANT")
        self.set(b["booking_id"], expires_at=datetime.utcnow() - timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError, "no longer available"):
            self.accept(b["booking_id"], "w_near")

    def test_accept_marks_instant_assigned(self):
        b = self.create(booking_type="INSTANT")
        self.accept(b["booking_id"], "w_near")
        self.assertEqual(self.booking(b["booking_id"])["status"], "ACCEPTED")
        ib = self.conn.exec_driver_sql("SELECT status FROM instant_bookings").fetchone()
        self.assertEqual(ib[0], "ASSIGNED")

    def test_worker_on_a_live_job_cannot_take_instant(self):
        insert(self.conn, "bookings", booking_id="live", customer_id="other", provider_id="p_near",
               service_id="s1", status="EN_ROUTE", scheduled_at=datetime.utcnow() - timedelta(hours=3))
        b = self.create(booking_type="INSTANT")
        self.assertEqual(self.offers("instant_job"), [])  # not even offered
        with self.assertRaisesRegex(ValueError, "Finish your current job"):
            self.accept(b["booking_id"], "w_near")

    def test_service_without_instant_refused(self):
        with self.assertRaisesRegex(ValueError, "isn't available for this service"):
            self.create(service_id="s2", booking_type="INSTANT")

    def test_closed_hours_refused(self):
        with patch("bookings.dispatch_rules.instant_open", return_value=False):
            with self.assertRaisesRegex(ValueError, "Instant booking is available"):
                self.create(booking_type="INSTANT")

    def test_pay_now_instant_waits_for_payment(self):
        b = self.create(booking_type="INSTANT", payment_mode="PAY_NOW")
        row = self.booking(b["booking_id"])
        self.assertEqual((row["dispatch_attempts"], row["expires_at"]), (0, None))
        self.assertEqual(self.offers("instant_job"), [])
        # tick leaves it to unpaid_expiry
        dispatch.tick_one(self.conn, row, now=datetime.now(timezone.utc) + timedelta(hours=1))
        self.assertEqual(self.booking(b["booking_id"])["status"], "PENDING")

    def test_legacy_instant_endpoint_uses_same_path(self):
        _, b = InstantBookingsService().create_instant(
            {"_user_id": "cust", "_role": "CUSTOMER", "service_id": "s1",
             "address_snapshot": dict(HOME), "scheduled_at": "2020-01-01T00:00:00Z"}, self.conn)
        row = self.booking(b["booking_id"])
        self.assertTrue(row["is_instant"])
        self.assertEqual(row["total_amount"], 19900)  # no surge
        self.assertEqual(self.offers("instant_job"), ["w_near"])

    def test_customer_poll_runs_the_round(self):
        b = self.create(booking_type="INSTANT")
        self.set(b["booking_id"], expires_at=datetime.utcnow() - timedelta(seconds=1))
        _, detail = self.books.get_detail({"_user_id": "cust", "_role": "CUSTOMER", "id": b["booking_id"]}, self.conn)
        self.assertEqual((detail["status"], detail["cancellation_reason"]), ("CANCELLED", "NO_EXPERT"))

    def test_tick_all(self):
        b = self.create(booking_type="INSTANT")
        self.set(b["booking_id"], expires_at=datetime.utcnow() - timedelta(seconds=1))
        self.assertEqual(dispatch.tick(self.conn), 1)
        self.assertEqual(self.booking(b["booking_id"])["status"], "CANCELLED")


class ScheduledBookingTests(DispatchTestBase):
    def slot(self, days=2, hour=12):
        d = (datetime.now(IST) + timedelta(days=days)).date()
        return ist(d.year, d.month, d.day, hour)

    def test_scheduled_offered_within_20km_with_deadline(self):
        slot = self.slot()
        b = self.create(scheduled_at=slot.isoformat())
        row = self.booking(b["booking_id"])
        self.assertFalse(row["is_instant"])
        self.assertEqual(rules.as_utc(row["expires_at"]), slot - timedelta(minutes=30))
        self.assertEqual(rules.as_utc(row["ends_at"]), slot + timedelta(minutes=60))
        self.assertEqual(sorted(self.offers("job_available")), ["w_mid", "w_near"])

    def test_outside_hours_refused(self):
        with self.assertRaisesRegex(ValueError, "between"):
            self.create(scheduled_at=self.slot(hour=23).isoformat())

    def test_reoffer_an_hour_before_tells_customer(self):
        slot = self.slot()
        b = self.create(scheduled_at=slot.isoformat())
        self.push.reset_mock()
        dispatch.tick_one(self.conn, self.booking(b["booking_id"]), now=slot - timedelta(minutes=50))
        self.assertEqual(sorted(self.offers("job_available")), ["w_mid", "w_near"])
        texts = [c.kwargs["title"] for c in self.push.call_args_list]
        self.assertIn("Still looking for your expert", texts)

    def test_expires_before_slot(self):
        slot = self.slot()
        b = self.create(scheduled_at=slot.isoformat())
        dispatch.tick_one(self.conn, self.booking(b["booking_id"]), now=slot - timedelta(minutes=20))
        self.assertEqual(self.booking(b["booking_id"])["cancellation_reason"], "NO_EXPERT")

    def test_overlapping_job_refused_and_hidden_from_feed(self):
        slot = self.slot()
        first = self.create(scheduled_at=slot.isoformat())
        self.accept(first["booking_id"], "w_near")
        second = self.create(scheduled_at=(slot + timedelta(minutes=60)).isoformat())
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.accept(second["booking_id"], "w_near")
        # The feed query itself is MySQL (JSON_UNQUOTE); this checks the clash filter on its rows.
        with patch.object(self.books.modal, "get_available_for_provider",
                          return_value=[self.booking(second["booking_id"])]):
            feed = self.books.list_available_for_provider({"_user_id": "w_near", "_role": "PROVIDER"}, self.conn)[1]
        self.assertNotIn(second["booking_id"], [b["booking_id"] for b in feed])
        # far enough apart is fine
        third = self.create(scheduled_at=(slot + timedelta(hours=3)).isoformat())
        self.accept(third["booking_id"], "w_near")

    def test_book_again_skips_direct_assign_on_clash(self):
        slot = self.slot()
        first = self.create(scheduled_at=slot.isoformat())
        self.accept(first["booking_id"], "w_near")
        again = self.create(scheduled_at=(slot + timedelta(minutes=30)).isoformat(), requested_provider_id="p_near")
        row = self.booking(again["booking_id"])
        self.assertEqual((row["status"], row["provider_id"]), ("PENDING", None))  # offered to everyone instead
        free = self.create(scheduled_at=(slot + timedelta(hours=4)).isoformat(), requested_provider_id="p_near")
        self.assertEqual(self.booking(free["booking_id"])["provider_id"], "p_near")

    def test_job_length_from_items(self):
        insert(self.conn, "sub_services", sub_service_id="ss1", service_id="s1", name="Tap", price=10000,
               is_active=True, duration_minutes=45)
        slot = self.slot()
        b = self.create(scheduled_at=slot.isoformat(), items=[{"sub_service_id": "ss1", "quantity": 2}])
        self.assertEqual(rules.as_utc(self.booking(b["booking_id"])["ends_at"]), slot + timedelta(minutes=90))

    def test_cancel_free_until_two_hours_before(self):
        accepted = datetime.utcnow() - timedelta(minutes=10)
        far = {"status": "ACCEPTED", "provider_id": "p", "accepted_at": accepted,
               "scheduled_at": datetime.utcnow() + timedelta(days=1)}
        near = {**far, "scheduled_at": datetime.utcnow() + timedelta(hours=1)}
        instant = {**far, "is_instant": True}
        self.assertEqual(cancellation_fee_for(far), 0)
        self.assertEqual(cancellation_fee_for(near), 5000)
        self.assertEqual(cancellation_fee_for(instant), 5000)


class OfferWithdrawalTests(DispatchTestBase):
    def offered_both(self):
        """Instant booking offered to w_near, then (next ring) w_mid."""
        b = self.create(booking_type="INSTANT")
        dispatch.tick_one(self.conn, self.booking(b["booking_id"]),
                          now=datetime.now(timezone.utc) + timedelta(minutes=3))
        return b["booking_id"]

    def taken(self):
        """(user_ids, data) of each job_taken silent push."""
        return [(c.args[1], c.args[2]) for c in self.silent.call_args_list
                if c.args[2].get("type") == "job_taken"]

    def test_offers_recorded_once_per_worker(self):
        bid = self.offered_both()
        dispatch.tick_one(self.conn, self.booking(bid),
                          now=datetime.now(timezone.utc) + timedelta(minutes=6))  # 12 km ring
        rows = self.conn.exec_driver_sql(
            "SELECT user_id FROM booking_offers WHERE booking_id=? ORDER BY user_id", (bid,)).fetchall()
        self.assertEqual([r[0] for r in rows], ["w_mid", "w_near"])
        dispatch._record_offers(self.conn, bid, ["w_near"])  # repeat offer: no duplicate, no error
        self.assertEqual(self.conn.exec_driver_sql(
            "SELECT COUNT(*) FROM booking_offers WHERE booking_id=?", (bid,)).scalar(), 2)

    def test_accept_withdraws_from_everyone_else(self):
        bid = self.offered_both()
        self.accept(bid, "w_near")
        self.assertEqual(self.taken(), [(["w_mid"], {"type": "job_taken", "booking_id": bid})])
        with self.assertRaisesRegex(ValueError, "no longer available"):
            self.accept(bid, "w_mid")

    def test_customer_cancel_while_pending_withdraws_from_all(self):
        bid = self.offered_both()
        self.books.cancel({"_user_id": "cust", "_role": "CUSTOMER", "id": bid}, self.conn)
        self.assertEqual(sorted(self.taken()[0][0]), ["w_mid", "w_near"])

    def test_expiry_withdraws(self):
        bid = self.offered_both()
        dispatch.tick_one(self.conn, self.booking(bid),
                          now=datetime.now(timezone.utc) + timedelta(minutes=11))
        self.assertEqual(self.booking(bid)["status"], "CANCELLED")
        self.assertEqual(sorted(self.taken()[0][0]), ["w_mid", "w_near"])

    def test_cancel_after_accept_sends_no_extra_job_taken(self):
        bid = self.offered_both()
        self.accept(bid, "w_near")
        self.silent.reset_mock()
        self.books.cancel({"_user_id": "cust", "_role": "CUSTOMER", "id": bid}, self.conn)
        self.assertEqual(self.taken(), [])


class SlotsApiTests(DispatchTestBase):
    def slots(self, **q):
        from services_catalog.services_service import ServicesService
        return ServicesService().slots({"id": "s1", **q}, self.conn)[1]

    def test_slots_and_instant(self):
        res = self.slots(lat=HOME["lat"], lng=HOME["lng"])
        self.assertTrue(res["instant_available"])
        self.assertEqual(res["workers_nearby"], 2)  # near + mid within 12 km
        self.assertEqual(len(res["slots"]), 28)

    def test_no_worker_nearby_closes_instant(self):
        res = self.slots(lat=HOME["lat"] + 1.0, lng=HOME["lng"])
        self.assertFalse(res["instant_available"])
        self.assertIn("No experts", res["instant_reason"])

    def test_non_instant_service(self):
        from services_catalog.services_service import ServicesService
        res = ServicesService().slots({"id": "s2"}, self.conn)[1]
        self.assertFalse(res["instant_available"])

    def test_day_beyond_horizon_has_no_slots(self):
        day = (datetime.now(IST) + timedelta(days=30)).date().isoformat()
        self.assertEqual(self.slots(date=day)["slots"], [])


if __name__ == "__main__":
    unittest.main()
