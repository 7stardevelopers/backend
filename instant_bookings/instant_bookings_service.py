"""Instant ("Now") bookings.

POST /instant-bookings is kept for older app builds: it is the same as
POST /bookings with booking_type=INSTANT — same pricing, coupons, payment mode and
address pin. The job is offered to workers in widening rounds and cancelled if
nobody accepts in time (bookings/dispatch.py). Nobody is assigned without accepting.
"""
from bookings.bookings_modal import BookingsMaster
from bookings import dispatch as dispatch_rounds
from instant_bookings.instant_bookings_modal import InstantBookingsMaster
from providers.providers_modal import ProvidersMaster


class InstantBookingsService:
    def __init__(self):
        self.modal = InstantBookingsMaster()
        self.booking_modal = BookingsMaster()

    def create_instant(self, obj, connection):
        from bookings.bookings_service import BookingsService
        obj = {k: v for k, v in obj.items() if k not in ("scheduled_at", "is_instant")}
        return BookingsService().create({**obj, "booking_type": "INSTANT"}, connection)

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
        if booking.get("status") == "PENDING":
            booking = dispatch_rounds.tick_one(connection, booking)
        from bookings.bookings_service import BookingsService
        BookingsService._hide_door_otp(booking, role)
        instant = self.modal.get_by_booking(connection, booking_id)
        return "success", {**booking, "instant": instant}
