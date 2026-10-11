import unittest
from datetime import datetime, timedelta

from tests.helpers import make_db, insert
from utilities.db_connection import get_table

from bookings.booking_pricing import (
    price_booking, apply_booking_side_effects, calculate_coupon_discount, release_booking_side_effects,
)
from bookings.bookings_modal import BookingsMaster
from bookings.bookings_service import ALLOWED_TRANSITIONS

FUTURE = datetime.utcnow() + timedelta(days=30)


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        insert(self.conn, "users", user_id="c1", role="CUSTOMER", coins_balance=100)
        insert(self.conn, "services", service_id="s1", base_price=49900, is_active=True)
        insert(self.conn, "sub_services", sub_service_id="ss1", service_id="s1", name="Sofa", price=20000, is_active=True)
        insert(self.conn, "sub_services", sub_service_id="ssX", service_id="s2", name="Other", price=1, is_active=True)

    def tearDown(self):
        self.conn.close()

    def test_base_price_when_no_items(self):
        p = price_booking(self.conn, "c1", "s1")
        self.assertEqual((p["sub_total"], p["total_amount"]), (49900, 49900))

    def test_items_priced_from_db_not_client(self):
        items = [{"sub_service_id": "ss1", "quantity": 2, "price": 1}]
        p = price_booking(self.conn, "c1", "s1", items=items)
        self.assertEqual(p["sub_total"], 40000)
        self.assertEqual(p["items"][0]["price"], 20000)

    def test_sub_service_from_other_service_ignored(self):
        p = price_booking(self.conn, "c1", "s1", items=[{"sub_service_id": "ssX", "quantity": 1}])
        self.assertEqual(p["sub_total"], 49900)

    def test_quantity_bounds(self):
        with self.assertRaises(ValueError):
            price_booking(self.conn, "c1", "s1", items=[{"sub_service_id": "ss1", "quantity": 0}])
        with self.assertRaises(ValueError):
            price_booking(self.conn, "c1", "s1", items=[{"sub_service_id": "ss1", "quantity": 999}])

    def test_coins_reduce_total_and_are_capped(self):
        p = price_booking(self.conn, "c1", "s1", coins_used=100)
        # 1 coin = ₹1 = 100 paise
        self.assertEqual((p["coins_used"], p["total_amount"]), (100, 39900))
        with self.assertRaises(ValueError):
            price_booking(self.conn, "c1", "s1", coins_used=101)

    def test_coupon_validated_and_reserved_once(self):
        insert(self.conn, "coupons", coupon_id="cp", code="TEN", title="t", type="PERCENT",
               value=10, max_uses=1, used_count=0, expires_at=FUTURE, is_active=True)
        p = price_booking(self.conn, "c1", "s1", coupon_id="cp")
        self.assertEqual((p["discount"], p["total_amount"]), (4990, 44910))
        apply_booking_side_effects(self.conn, "c1", "b1", p)
        insert(self.conn, "users", user_id="c2", role="CUSTOMER")
        with self.assertRaises(ValueError):  # max_uses reached
            price_booking(self.conn, "c2", "s1", coupon_id="cp")

    def test_expired_coupon_rejected(self):
        insert(self.conn, "coupons", coupon_id="old", code="OLD", title="t", type="FLAT",
               value=100, expires_at=datetime.utcnow() - timedelta(days=1), is_active=True)
        with self.assertRaises(ValueError):
            price_booking(self.conn, "c1", "s1", coupon_id="old")

    def test_subscription_quota_enforced(self):
        insert(self.conn, "subscription_plans", plan_id="p1", name="Basic", price=1,
               bookings_included=1, discount_pct=10)
        insert(self.conn, "user_subscriptions", subscription_id="sub1", user_id="c1", plan_id="p1",
               status="ACTIVE", expires_at=FUTURE, bookings_used=0)
        p = price_booking(self.conn, "c1", "s1")
        self.assertEqual(p["discount"], 4990)
        apply_booking_side_effects(self.conn, "c1", "b1", p)
        self.assertEqual(price_booking(self.conn, "c1", "s1")["discount"], 0)

    def test_cancel_returns_coupon_coins_and_quota(self):
        insert(self.conn, "coupons", coupon_id="cp", code="ONE", title="t", type="FLAT",
               value=500, max_uses=1, used_count=0, expires_at=FUTURE, is_active=True)
        insert(self.conn, "subscription_plans", plan_id="p1", name="Basic", price=1,
               bookings_included=1, discount_pct=10)
        insert(self.conn, "user_subscriptions", subscription_id="sub1", user_id="c1", plan_id="p1",
               status="ACTIVE", starts_at=datetime.utcnow() - timedelta(days=1),
               expires_at=FUTURE, bookings_used=0)
        p = price_booking(self.conn, "c1", "s1", coupon_id="cp", coins_used=40)
        apply_booking_side_effects(self.conn, "c1", "b1", p)
        booking = {"booking_id": "b1", "customer_id": "c1", "coupon_id": "cp", "created_at": datetime.utcnow(),
                   "subscription_id": p["subscription"]["subscription_id"]}
        release_booking_side_effects(self.conn, booking)

        from referrals.referrals_modal import ReferralsMaster
        self.assertEqual(ReferralsMaster().get_balance(self.conn, "c1"), 100)
        again = price_booking(self.conn, "c1", "s1", coupon_id="cp")   # coupon usable again
        self.assertEqual(again["discount"], 500 + 4990)                 # and quota restored

    def test_cancelling_a_booking_that_used_no_quota_gives_none_back(self):
        # unpaid booking made after the quota ran out, then expired — no free quota
        insert(self.conn, "subscription_plans", plan_id="p1", name="Basic", price=1,
               bookings_included=1, discount_pct=10)
        insert(self.conn, "user_subscriptions", subscription_id="sub1", user_id="c1", plan_id="p1",
               status="ACTIVE", starts_at=datetime.utcnow() - timedelta(days=1),
               expires_at=FUTURE, bookings_used=1)
        release_booking_side_effects(self.conn, {"booking_id": "b9", "customer_id": "c1",
                                                 "created_at": datetime.utcnow(), "subscription_id": None})
        used = self.conn.exec_driver_sql("SELECT bookings_used FROM user_subscriptions").scalar()
        self.assertEqual(used, 1)

    def test_percent_discount_capped_at_100(self):
        coupon = {"type": "PERCENT", "value": 150, "max_discount": None}
        self.assertEqual(calculate_coupon_discount(coupon, 1000), 1000)


class BookingStateTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.modal = BookingsMaster()
        insert(self.conn, "bookings", booking_id="b1", customer_id="c1", provider_id="p1",
               status="EN_ROUTE", door_otp="1234", door_otp_verified=False)

    def tearDown(self):
        self.conn.close()

    def test_door_otp_starts_job_once(self):
        self.assertFalse(self.modal.verify_door_otp(self.conn, "b1", "0000"))
        self.assertTrue(self.modal.verify_door_otp(self.conn, "b1", "1234"))
        self.assertEqual(self.modal.read_one(self.conn, "b1")["status"], "IN_PROGRESS")
        self.assertFalse(self.modal.verify_door_otp(self.conn, "b1", "1234"))

    def test_conditional_status_update(self):
        self.modal.update_status(self.conn, "b1", "CANCELLED", expected_status="EN_ROUTE")
        with self.assertRaises(ValueError):
            self.modal.update_status(self.conn, "b1", "COMPLETED", expected_status="IN_PROGRESS")

    def test_provider_cannot_skip_door_otp(self):
        provider_targets = {t for targets in ALLOWED_TRANSITIONS["PROVIDER"].values() for t in targets}
        self.assertNotIn("IN_PROGRESS", provider_targets)
        self.assertNotIn("ACCEPTED", provider_targets)


if __name__ == "__main__":
    unittest.main()


class BookingAddressPinTests(unittest.TestCase):
    """Bookings must carry coordinates so workers can navigate and be matched."""

    def setUp(self):
        self.conn = make_db()
        insert(self.conn, "user_addresses", address_id="a1", user_id="c1", full_address="12 MG Road, Vizag",
               lat=17.72, lng=83.31, is_deleted=False)
        insert(self.conn, "user_addresses", address_id="a2", user_id="c1", full_address="No pin street",
               is_deleted=False)

    def tearDown(self):
        self.conn.close()

    def test_snapshot_coords_kept(self):
        from bookings.bookings_service import _pinned_snapshot
        snap = _pinned_snapshot(self.conn, "c1", "a1", {"full_address": "x", "lat": 1.5, "lng": 2.5})
        self.assertEqual((snap["lat"], snap["lng"]), (1.5, 2.5))

    def test_null_snapshot_coords_filled_from_saved_address(self):
        from bookings.bookings_service import _pinned_snapshot
        snap = _pinned_snapshot(self.conn, "c1", "a1", {"full_address": "12 MG Road", "lat": None, "lng": None})
        self.assertEqual((snap["lat"], snap["lng"]), (17.72, 83.31))

    def test_other_users_address_not_used(self):
        from bookings.bookings_service import _pinned_snapshot
        snap = _pinned_snapshot(self.conn, "intruder", "a1", {"full_address": "x", "lat": None})
        self.assertNotIn("lat", snap)

    def test_unpinnable_address_drops_null_keys(self):
        from unittest.mock import patch
        from bookings.bookings_service import _pinned_snapshot
        with patch("places.places_service.geocode_address", return_value=None):
            snap = _pinned_snapshot(self.conn, "c1", "a2", {"full_address": "No pin street", "lat": None, "lng": None})
        self.assertEqual(snap, {"full_address": "No pin street"})

    def test_geocoded_pin_saved_on_address(self):
        from unittest.mock import patch
        from bookings.bookings_service import _pinned_snapshot
        with patch("places.places_service.geocode_address", return_value=(17.7, 83.3)):
            snap = _pinned_snapshot(self.conn, "c1", "a2", {"full_address": "No pin street"})
        self.assertEqual((snap["lat"], snap["lng"]), (17.7, 83.3))
        row = self.conn.execute(get_table("user_addresses").select()
                                .where(get_table("user_addresses").c.address_id == "a2")).fetchone()
        self.assertEqual((row.lat, row.lng), (17.7, 83.3))


class JobPushTextTests(unittest.TestCase):
    def test_when_label_in_ist(self):
        from datetime import timezone
        from bookings.bookings_service import _job_when
        now = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
        self.assertEqual(_job_when("2026-10-11T11:00:00Z", now), "Today, 4:30 PM")
        self.assertEqual(_job_when(datetime(2026, 10, 12, 3, 30), now), "Tomorrow, 9:00 AM")
        self.assertEqual(_job_when("2026-10-18T05:30:00Z", now), "Sun 18 Oct, 11:00 AM")
        self.assertEqual(_job_when(None, now), "As soon as possible")
        self.assertEqual(_job_when("garbage", now), "As soon as possible")

    def test_push_body_names_service_and_time(self):
        from bookings.bookings_service import _job_push_body
        body = _job_push_body({"service_snapshot": '{"name": "AC Repair"}', "scheduled_at": None})
        self.assertEqual(body, "AC Repair • As soon as possible. Tap to view and accept.")


class NotificationListTests(unittest.TestCase):
    def test_created_at_is_utc_with_z_and_data_is_object(self):
        from notifications.notifications_service import _present_notification
        n = _present_notification({"created_at": datetime(2026, 10, 11, 6, 0, 5), "data": '{"booking_id": "b1"}'})
        self.assertEqual(n["created_at"], "2026-10-11T06:00:05.000Z")
        self.assertEqual(n["data"], {"booking_id": "b1"})
