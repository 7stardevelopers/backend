from datetime import datetime, timedelta, timezone

from bookings.booking_pricing import price_booking
from instant_bookings.instant_bookings_modal import InstantBookingsMaster
from instant_bookings.instant_bookings_validator import CreateInstantBookingSchema
from bookings.bookings_modal import BookingsMaster
from notifications.notifications_service import NotificationsService
from providers.providers_modal import ProvidersMaster, is_registered_worker, WORKER_CANNOT_BOOK
from utilities.common_table_elements import now_utc

IST = timezone(timedelta(hours=5, minutes=30))

PEAK_HOURS = [(9, 12), (18, 21)]
SURGE_PCT = 20


def _is_peak_hour() -> bool:
    hour = datetime.now(IST).hour
    return any(start <= hour < end for start, end in PEAK_HOURS)


class InstantBookingsService:
    def __init__(self):
        self.modal = InstantBookingsMaster()
        self.booking_modal = BookingsMaster()
        self.notif = NotificationsService()

    def create_instant(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role == "PROVIDER" or is_registered_worker(connection, user_id):
            raise PermissionError(WORKER_CANNOT_BOOK)
        if role != "CUSTOMER":
            raise PermissionError("Only customers can create instant bookings")

        data = CreateInstantBookingSchema(**obj)
        surge = _is_peak_hour()
        surge_pct = SURGE_PCT if surge else 0

        pricing = price_booking(connection, user_id, data.service_id, items=data.items)
        sub_total = pricing["sub_total"]
        if surge:
            sub_total = int(sub_total * (1 + surge_pct / 100))

        booking_data = {
            "customer_id": user_id,
            "service_id": data.service_id,
            "scheduled_at": now_utc(),
            "address_id": data.address_id,
            "address_snapshot": data.address_snapshot,
            "sub_total": sub_total,
            "discount": 0,
            "total_amount": sub_total,
            "is_instant": True,
            "customer_notes": data.customer_notes,
            "status": "PENDING",
            "payment_status": "PENDING",
        }
        booking = self.booking_modal.create(connection, booking_data)
        if pricing["items"]:
            self.booking_modal.create_items(connection, booking["booking_id"], pricing["items"])
        instant = self.modal.create(connection, booking["booking_id"], surge_applied=surge, surge_pct=surge_pct)

        try:
            from providers.provider_matching import match_provider
            provider = match_provider(connection, booking)
            if provider and self.booking_modal.claim_booking(connection, booking["booking_id"], provider["provider_id"]):
                instant_rec = self.modal.get_by_booking(connection, booking["booking_id"])
                self.modal.update(connection, instant_rec["instant_id"], {
                    "dispatched_at": now_utc(),
                    "provider_assigned_at": now_utc(),
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
        user_id = obj.pop("_user_id", None)
        role = obj.pop("_role", None)
        if not user_id:
            raise PermissionError("Authentication required")
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.booking_modal.read_one(connection, booking_id)
        if role not in ("ADMIN", "SUPPORT"):
            is_customer = str(booking.get("customer_id")) == str(user_id)
            prov = ProvidersMaster().find_by_user_id(connection, user_id) if role == "PROVIDER" else None
            is_provider = bool(prov) and str(prov["provider_id"]) == str(booking.get("provider_id"))
            if not is_customer and not is_provider:
                raise PermissionError("Access denied")
        from bookings.bookings_service import BookingsService
        BookingsService._hide_door_otp(booking, role)
        instant = self.modal.get_by_booking(connection, booking_id)
        return "success", {**booking, "instant": instant}
