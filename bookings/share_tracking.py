"""Share live tracking (Rapido's "share ride details") for a booking.

POST /bookings/{id}/share   customer → { token, path, expires_in_hours }
GET  /track/{token}         public JSON snapshot (polled by the page)
GET  /track/{token}/page    public read-only map page

The snapshot carries only what a family member needs: the expert's first
name, the service, status, ETA and — only while the booking is live-trackable —
the expert's position and the destination pin. No phone numbers, no address
text. Links are booking-scoped JWTs (typ "track") that expire after
TRACK_TOKEN_HOURS and can't be used against the rest of the API.
"""
import os

from sqlalchemy import text

from bookings.bookings_modal import BookingsMaster
from bookings.live_tracking import is_live_tracking, booking_destination, LIVE_STATUSES
from providers.providers_modal import ProvidersMaster
from utilities.auth_tokens import issue_track_token, decode_track_token, TRACK_TOKEN_HOURS

_PAGE_PATH = os.path.join(os.path.dirname(__file__), "track_page.html")


class ShareTrackingService:
    def __init__(self):
        self.modal = BookingsMaster()

    def create_link(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.modal.read_one(connection, booking_id)
        if str(booking.get("customer_id")) != str(user_id):
            raise PermissionError("Only the customer can share tracking")
        if booking.get("status") not in ("ACCEPTED",) + LIVE_STATUSES:
            raise ValueError("Tracking can be shared once an expert is assigned")
        token = issue_track_token(booking_id)
        return "success", {
            "token": token,
            "path": f"/track/{token}/page",
            "expires_in_hours": TRACK_TOKEN_HOURS,
        }

    def public_snapshot(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        booking_id = decode_track_token(obj.get("token", ""))
        booking = self.modal.read_one(connection, booking_id)
        live = is_live_tracking(booking, conn=connection)

        expert = None
        if booking.get("provider_id"):
            row = connection.execute(text("""
                SELECT u.name, p.avg_rating FROM providers p
                JOIN users u ON u.user_id = p.user_id WHERE p.provider_id = :pid
            """), {"pid": booking["provider_id"]}).fetchone()
            if row:
                expert = {
                    "first_name": (row.name or "Your expert").split()[0],
                    "rating": round(float(row.avg_rating or 0), 1),
                }
        svc = connection.execute(
            text("SELECT name FROM services WHERE service_id = :sid"), {"sid": booking.get("service_id")}
        ).fetchone()

        location = destination = eta = None
        if live:
            loc = ProvidersMaster().get_location(connection, booking["provider_id"])
            destination = booking_destination(booking)
            if loc and loc.get("lat") is not None:
                location = {"lat": float(loc["lat"]), "lng": float(loc["lng"]), "updated_at": loc.get("updated_at")}
                if booking["status"] == "EN_ROUTE" and destination:
                    from providers.providers_service import _cached_road_eta
                    eta = _cached_road_eta(booking_id, (location["lat"], location["lng"]), destination)

        return "success", {
            "status": booking.get("status"),
            "live": live,
            "service_name": svc.name if svc else None,
            "expert": expert,
            "location": location,
            "destination": {"lat": destination[0], "lng": destination[1]} if destination else None,
            "eta": eta,
        }

    def public_page(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        # Validate up front so a bad/expired link shows a clear message instead of a blank map.
        try:
            decode_track_token(obj.get("token", ""))
            error = ""
        except PermissionError as e:
            error = str(e)
        with open(_PAGE_PATH, encoding="utf-8") as f:
            page = f.read()
        return "html", page.replace("__LINK_ERROR__", error)
