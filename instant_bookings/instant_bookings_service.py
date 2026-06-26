from datetime import datetime, timezone

from instant_bookings.instant_bookings_modal import InstantBookingsMaster
from instant_bookings.instant_bookings_validator import CreateInstantBookingSchema
from bookings.bookings_modal import BookingsMaster
from notifications.notifications_service import NotificationsService

PEAK_HOURS = [(9, 12), (18, 21)]
SURGE_PCT = 20


def _is_peak_hour() -> bool:
    now = datetime.now(timezone.utc)
    hour = now.hour + 5  # IST offset approx
    hour = hour % 24
    return any(start <= hour < end for start, end in PEAK_HOURS)


class InstantBookingsService:
    def __init__(self):
        self.modal = InstantBookingsMaster()
        self.booking_modal = BookingsMaster()
        self.notif = NotificationsService()

    def create_instant(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "CUSTOMER":
            raise PermissionError("Only customers can create instant bookings")

        data = CreateInstantBookingSchema(**obj)
        surge = _is_peak_hour()
        surge_pct = SURGE_PCT if surge else 0

        total = data.total_amount
        if surge:
            total = int(total * (1 + surge_pct / 100))

        from datetime import datetime, timezone
        booking_data = {
            "customer_id": user_id,
            "service_id": data.service_id,
            "scheduled_at": datetime.now(timezone.utc),
            "address_id": data.address_id,
            "address_snapshot": data.address_snapshot,
            "sub_total": data.sub_total,
            "discount": 0,
            "total_amount": total,
            "is_instant": True,
            "customer_notes": data.customer_notes,
            "status": "PENDING",
            "payment_status": "PENDING",
        }
        booking = self.booking_modal.create(connection, booking_data)
        instant = self.modal.create(connection, booking["booking_id"], surge_applied=surge, surge_pct=surge_pct)

        try:
            from providers.provider_matching import match_provider
            provider = match_provider(connection, booking)
            if provider:
                self.booking_modal.assign_provider(connection, booking["booking_id"], provider["provider_id"])
                instant_rec = self.modal.get_by_booking(connection, booking["booking_id"])
                self.modal.update(connection, instant_rec["instant_id"], {
                    "dispatched_at": __import__("utilities.common_table_elements", fromlist=["now_utc"]).now_utc(),
                    "provider_assigned_at": __import__("utilities.common_table_elements", fromlist=["now_utc"]).now_utc(),
                    "status": "ASSIGNED",
                })
                self.notif.send_push(
                    connection=connection,
                    user_ids=[provider["user_id"]],
                    title="⚡ Instant Job Request!",
                    body=f"New instant booking — customer needs you now",
                    data={"type": "instant_job", "booking_id": booking["booking_id"]},
                )
        except Exception as e:
            print(f"[Instant] Provider matching failed (non-fatal): {e}")

        self.notif.send_push(
            connection=connection,
            user_ids=[user_id],
            title="⚡ Instant booking placed!",
            body="Finding an expert near you — ETA within 60 minutes.",
            data={"type": "instant_booking_confirmed", "booking_id": booking["booking_id"]},
        )
        return "created", {**booking, "instant": instant, "surge_applied": surge, "surge_pct": surge_pct}

    def get_status(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.booking_modal.read_one(connection, booking_id)
        instant = self.modal.get_by_booking(connection, booking_id)
        return "success", {**booking, "instant": instant}
