import hmac
import os
import razorpay
from datetime import datetime, timezone
from sqlalchemy import text

from bookings.bookings_modal import BookingsMaster
from bookings.bookings_validator import (
    CreateBookingSchema, UpdateStatusSchema, VerifyDoorOTPSchema, AddTipSchema
)
from bookings.booking_pricing import price_booking, apply_booking_side_effects, release_booking_side_effects
from notifications.notifications_service import NotificationsService
from payments.payment_modal import PaymentMaster
from providers.provider_matching import match_provider
from providers.providers_modal import ProvidersMaster
from utilities.common_table_elements import new_uuid, now_utc
from utilities.db_connection import get_table

DOOR_OTP_TTL_SECONDS = 3600   # 1 hour — regenerated on EN_ROUTE anyway, this is a backstop
MAX_OTP_ATTEMPTS = 5

# PENDING → ACCEPTED goes through accept_booking (claim) and
# EN_ROUTE/ACCEPTED → IN_PROGRESS only through verify_door_otp, so neither the
# claim nor the door OTP can be bypassed via PATCH /status. Provider COMPLETED
# goes through complete().
ALLOWED_TRANSITIONS = {
    "PROVIDER": {
        "ACCEPTED":    ["EN_ROUTE"],
    },
    "CUSTOMER": {
        "PENDING":  ["CANCELLED"],
        "ACCEPTED": ["CANCELLED"],
    },
    "ADMIN": {
        "PENDING":  ["CANCELLED"],
        "ACCEPTED": ["CANCELLED"],
        "EN_ROUTE": ["CANCELLED"],
        "IN_PROGRESS": ["COMPLETED", "CANCELLED"],
    },
}


class BookingsService:
    def __init__(self):
        self.modal = BookingsMaster()
        self.notif = NotificationsService()

    @staticmethod
    def _hide_door_otp(booking, role):
        # A provider must never be able to read the OTP directly out of a
        # response — it's the customer's spoken confirmation that the right
        # person is at the door. Only strip it for that role; customer/admin
        # still need it.
        if role == "PROVIDER":
            booking.pop("door_otp", None)
        return booking

    def create(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role not in ("CUSTOMER",):
            raise PermissionError("Only customers can create bookings")

        validated = CreateBookingSchema(**obj)
        pricing = price_booking(
            connection, user_id, validated.service_id,
            items=validated.items, coupon_id=validated.coupon_id, coins_used=validated.coins_used,
        )

        booking_data = {
            "customer_id": user_id,
            "service_id": validated.service_id,
            "scheduled_at": validated.scheduled_at,
            "address_id": validated.address_id,
            "address_snapshot": validated.address_snapshot,
            "service_snapshot": validated.service_snapshot,
            "sub_total": pricing["sub_total"],
            "discount": pricing["discount"],
            "total_amount": pricing["total_amount"],
            "coupon_id": validated.coupon_id if pricing["coupon"] else None,
            "is_instant": validated.is_instant,
            "customer_notes": validated.customer_notes,
            "requested_provider_id": validated.requested_provider_id,
            "status": "PENDING",
            "payment_status": "PENDING",
        }
        booking = self.modal.create(connection, booking_data)
        apply_booking_side_effects(connection, user_id, booking["booking_id"], pricing)
        if pricing["items"]:
            self.modal.create_items(connection, booking["booking_id"], pricing["items"])

        # "Book again" — try to directly assign the requested provider if
        # they're approved, currently online, and still offer this service.
        # Falls back to the normal broadcast-to-nearby-providers flow below.
        direct_assigned = False
        if validated.requested_provider_id:
            try:
                prov = ProvidersMaster().find_by_id(connection, validated.requested_provider_id)
                if prov and prov.get("status") == "APPROVED" and prov.get("is_available"):
                    ps_t = get_table("provider_services")
                    offers_service = connection.execute(
                        ps_t.select()
                        .where(ps_t.c.provider_id == validated.requested_provider_id)
                        .where(ps_t.c.service_id == validated.service_id)
                    ).fetchone()
                    if offers_service:
                        direct_assigned = self.modal.claim_booking(
                            connection, booking["booking_id"], validated.requested_provider_id
                        )
                        if direct_assigned:
                            booking = self.modal.read_one(connection, booking["booking_id"])
                            self.notif.send_push(
                                connection=connection,
                                user_ids=[prov["user_id"]],
                                title="Repeat Customer!",
                                body="A customer you've worked with before requested you again.",
                                data={"type": "job_available", "booking_id": booking["booking_id"]},
                            )
            except Exception as e:
                print(f"[Rebook] Direct-assign failed (non-fatal, falling back to broadcast): {e}")

        # Notify nearby available providers — booking stays PENDING, first to accept gets it
        # (skipped if the requested provider was already directly assigned above)
        if not direct_assigned:
            try:
                from providers.provider_matching import haversine
                addr = booking.get("address_snapshot") or {}
                b_lat = addr.get("lat")
                b_lng = addr.get("lng")
                candidates = ProvidersMaster().get_available_for_service(connection, booking["service_id"])
                nearby_user_ids = []
                for p in candidates:
                    p_lat = p.get("last_lat")
                    p_lng = p.get("last_lng")
                    if p_lat and p_lng and b_lat and b_lng:
                        if haversine(float(b_lat), float(b_lng), float(p_lat), float(p_lng)) <= 20:
                            nearby_user_ids.append(p["user_id"])
                    else:
                        nearby_user_ids.append(p["user_id"])
                if nearby_user_ids:
                    self.notif.send_push(
                        connection=connection,
                        user_ids=nearby_user_ids,
                        title="New Job Near You",
                        body="A new job is available in your area. Tap to view.",
                        data={"type": "job_available", "booking_id": booking["booking_id"]},
                    )
            except Exception as e:
                print(f"[Notify] nearby push failed (non-fatal): {e}")

        self.notif.send_push(
            connection=connection,
            user_ids=[user_id],
            title="Booking confirmed!",
            body="We're finding the best expert for you.",
            data={"type": "booking_confirmed", "booking_id": booking["booking_id"]},
        )
        booking["coins_used"] = pricing["coins_used"]
        self._hide_door_otp(booking, role)
        return "created", booking

    def list_mine(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        page = int(obj.get("page", 1))
        status = obj.get("status")
        filters = {}
        if status:
            filters["status"] = status
        if role == "CUSTOMER":
            filters["customer_id"] = user_id
        elif role == "PROVIDER":
            provider = ProvidersMaster().find_by_user_id(connection, user_id)
            if not provider:
                return "success", []
            provider_bookings = self.modal.read_for_provider(
                connection, provider["provider_id"],
                status_filter=status,
                limit=20, offset=(page - 1) * 20,
            )
            for b in provider_bookings:
                self._hide_door_otp(b, role)
            return "success", provider_bookings
        elif role in ("ADMIN", "SUPPORT"):
            pass
        else:
            raise PermissionError("Unauthorized")
        bookings = self.modal.read(connection, filters, limit=20, offset=(page - 1) * 20)
        return "success", bookings

    def get_detail(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.modal.read_one(connection, booking_id)
        if role not in ("ADMIN", "SUPPORT"):
            is_customer = str(booking.get("customer_id")) == str(user_id)
            is_provider = False
            if booking.get("provider_id"):
                prov = ProvidersMaster().find_by_user_id(connection, user_id)
                if prov and str(prov["provider_id"]) == str(booking["provider_id"]):
                    is_provider = True
            elif role == "PROVIDER" and booking.get("status") == "PENDING" and _is_approved_provider(connection, user_id):
                # Unclaimed broadcast job — any provider may view it before
                # deciding to accept (offering the service is checked by
                # get_available_for_provider; this just allows the detail
                # screen to load once they've tapped in from that list).
                is_provider = True
            if not is_customer and not is_provider:
                raise PermissionError("Access denied")
        booking["items"] = self.modal.get_items(connection, booking_id)
        if booking.get("provider_id"):
            try:
                from sqlalchemy import text as _text
                row = connection.execute(_text("""
                    SELECT u.name, u.photo_url, p.avg_rating, p.total_reviews
                    FROM providers p
                    JOIN users u ON u.user_id = p.user_id
                    WHERE p.provider_id = :pid
                """), {"pid": booking["provider_id"]}).fetchone()
                if row:
                    booking["provider_name"]  = row.name
                    booking["provider_photo"] = row.photo_url
                    booking["provider_rating"] = float(row.avg_rating or 0)
                loc = ProvidersMaster().get_location(connection, booking["provider_id"])
                if loc:
                    booking["provider_lat"] = float(loc["lat"]) if loc.get("lat") else None
                    booking["provider_lng"] = float(loc["lng"]) if loc.get("lng") else None
            except Exception:
                pass
        if booking.get("customer_id"):
            try:
                from sqlalchemy import text as _text
                row = connection.execute(_text(
                    "SELECT name, photo_url FROM users WHERE user_id = :uid"
                ), {"uid": booking["customer_id"]}).fetchone()
                if row:
                    booking["customer_name"]  = row.name
                    booking["customer_photo"] = row.photo_url
            except Exception:
                pass
        try:
            from reviews.reviews_modal import ReviewsMaster
            rm = ReviewsMaster()
            review_row = rm.find_by_booking(connection, booking_id)
            booking["review"] = dict(review_row._mapping) if review_row else None
            pr_row = rm.find_provider_review_by_booking(connection, booking_id)
            booking["provider_review"] = dict(pr_row._mapping) if pr_row else None
        except Exception:
            booking["review"] = None
            booking["provider_review"] = None
        self._hide_door_otp(booking, role)
        return "success", booking

    def list_past_providers(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        service_id = obj.get("service_id")
        if not service_id:
            raise ValueError("service_id is required")
        return "success", self.modal.list_past_providers(connection, user_id, service_id)

    def list_available_for_provider(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        prov_master = ProvidersMaster()
        provider = prov_master.find_by_user_id(connection, user_id)
        if not provider or provider.get("status") != "APPROVED":
            return "success", []

        lat = obj.get("lat")
        lng = obj.get("lng")
        if lat is not None and lng is not None:
            try:
                lat, lng = float(lat), float(lng)
                prov_master.upsert_location(connection, provider["provider_id"], lat, lng)
            except (TypeError, ValueError):
                lat = lng = None

        if lat is None:
            loc = prov_master.get_location(connection, provider["provider_id"])
            if loc and loc.get("lat"):
                lat = float(loc["lat"])
                lng = float(loc["lng"])

        bookings = self.modal.get_available_for_provider(connection, provider["provider_id"], lat, lng)
        for b in bookings:
            self._hide_door_otp(b, role)
        return "success", bookings

    def accept_booking(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        booking_id = obj.get("id")
        provider = ProvidersMaster().find_by_user_id(connection, user_id)
        if not provider:
            raise ValueError("Provider profile not found")
        if provider.get("status") != "APPROVED":
            raise PermissionError("Your provider account is not approved yet")
        claimed = self.modal.claim_booking(connection, booking_id, provider["provider_id"])
        if not claimed:
            raise ValueError("Booking is no longer available — another provider may have accepted it")
        booking = self.modal.read_one(connection, booking_id)
        self.notif.send_push(
            connection=connection,
            user_ids=[booking["customer_id"]],
            title="Expert on the way!",
            body="Your booking has been accepted. The expert is on the way.",
            data={"type": "booking_accepted", "booking_id": booking_id},
        )
        self._hide_door_otp(booking, role)
        return "success", booking

    def update_status(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        new_status = obj.get("status")
        booking = self.modal.read_one(connection, booking_id)

        if role == "PROVIDER":
            prov = ProvidersMaster().find_by_user_id(connection, user_id)
            if not prov or str(prov["provider_id"]) != str(booking.get("provider_id")):
                raise PermissionError("You are not assigned to this booking")
        elif role == "CUSTOMER":
            if str(booking.get("customer_id")) != str(user_id):
                raise PermissionError("Access denied")
        elif role != "ADMIN":
            raise PermissionError("Access denied")

        allowed = ALLOWED_TRANSITIONS.get(role, {}).get(booking["status"], [])
        if new_status not in allowed:
            raise ValueError(f"Cannot transition from {booking['status']} to {new_status}")
        if new_status == "CANCELLED":
            # Same path as POST /cancel so refunds/notifications always run
            updated = self._do_cancel(connection, booking)
            self._hide_door_otp(updated, role)
            return "success", updated
        updated = self.modal.update_status(connection, booking_id, new_status, expected_status=booking["status"])
        if new_status == "EN_ROUTE":
            # Fresh OTP + reset lockout/TTL clock — the booking may have been
            # created hours or days ago, and any earlier failed attempts
            # shouldn't count against the customer once the job actually starts.
            self.modal.regenerate_door_otp(connection, booking_id)
            updated = self.modal.read_one(connection, booking_id)
        self._notify_status_change(connection, updated, new_status)
        self._hide_door_otp(updated, role)
        return "success", updated

    def complete(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        proof_photos = obj.get("proof_photos", [])
        booking = self.modal.read_one(connection, booking_id)
        prov = ProvidersMaster().find_by_user_id(connection, user_id)
        if role != "PROVIDER" or not prov or str(prov["provider_id"]) != str(booking.get("provider_id")):
            raise PermissionError("You are not assigned to this booking")
        if booking["status"] != "IN_PROGRESS":
            raise ValueError("Booking must be IN_PROGRESS to complete")
        if proof_photos:
            self.modal.update_proof_photos(connection, booking_id, proof_photos)
        updated = self.modal.update_status(connection, booking_id, "COMPLETED", expected_status="IN_PROGRESS")
        self._notify_status_change(connection, updated, "COMPLETED")
        return "success", {"message": "Booking completed"}

    def cancel(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.modal.read_one(connection, booking_id)
        if str(booking.get("customer_id")) != str(user_id) and role not in ("ADMIN",):
            raise PermissionError("Access denied")
        if booking["status"] not in ("PENDING", "ACCEPTED"):
            raise ValueError(f"Cannot cancel booking in {booking['status']} status")
        return "success", self._do_cancel(connection, booking)

    def _do_cancel(self, connection, booking):
        booking_id = booking["booking_id"]
        updated = self.modal.update_status(
            connection, booking_id, "CANCELLED", expected_status=booking["status"]
        )
        release_booking_side_effects(connection, booking)
        self._notify_status_change(connection, updated, "CANCELLED")
        if booking.get("provider_id"):
            try:
                prov = ProvidersMaster().find_by_id(connection, booking["provider_id"])
                if prov:
                    self.notif.send_push(
                        connection=connection,
                        user_ids=[prov["user_id"]],
                        title="Booking Cancelled",
                        body="A booking assigned to you has been cancelled.",
                        data={"type": "booking_update", "booking_id": booking_id, "status": "CANCELLED"},
                    )
            except Exception as e:
                print(f"[Cancel] Provider notification failed (non-fatal): {e}")

        if booking.get("payment_status") == "PAID" and booking.get("payment_id"):
            try:
                pay_modal = PaymentMaster()
                payment = pay_modal.find_payment(connection, payment_id=booking["payment_id"])
                if payment and payment.get("razorpay_payment_id"):
                    client = razorpay.Client(auth=(
                        os.environ.get("RAZORPAY_KEY_ID", ""),
                        os.environ.get("RAZORPAY_KEY_SECRET", ""),
                    ))
                    client.payment.refund(payment["razorpay_payment_id"], {"amount": payment["amount"]})
                    pay_modal.update_payment(connection, payment["payment_id"], {"status": "REFUNDED"})
            except Exception as e:
                print(f"[Cancel] Refund initiation failed (non-fatal): {e}")

        return updated

    def verify_door_otp(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        if role != "PROVIDER":
            raise PermissionError("Only providers can verify door OTP")
        data = VerifyDoorOTPSchema(**{k: v for k, v in obj.items() if not k.startswith("_") and k not in ("id",)})

        booking = self.modal.read_one(connection, booking_id)
        prov = ProvidersMaster().find_by_user_id(connection, user_id)
        if not prov or str(prov["provider_id"]) != str(booking.get("provider_id")):
            raise PermissionError("You are not assigned to this booking")

        if booking["status"] not in ("ACCEPTED", "EN_ROUTE") or booking.get("door_otp_verified"):
            raise ValueError(f"Cannot start a job that is {booking['status']}")

        if (booking.get("otp_attempt_count") or 0) >= MAX_OTP_ATTEMPTS:
            raise ValueError("Too many incorrect attempts. Ask the customer to resend the OTP.")

        generated_at = booking.get("door_otp_generated_at") or booking.get("created_at")
        if generated_at:
            now = datetime.utcnow() if generated_at.tzinfo is None else datetime.now(timezone.utc)
            if (now - generated_at).total_seconds() > DOOR_OTP_TTL_SECONDS:
                raise ValueError("This OTP has expired. Ask the customer to resend it.")

        # Compare before writing anything: a wrong guess raises, which rolls back
        # this request's transaction — so the attempt is recorded on its own
        # committed connection, otherwise the lockout would never take effect.
        if not hmac.compare_digest(str(booking.get("door_otp") or ""), data.otp):
            attempts = self.modal.record_failed_otp_attempt_committed(booking_id)
            remaining = max(0, MAX_OTP_ATTEMPTS - attempts)
            if remaining:
                raise ValueError(f"Invalid door OTP. {remaining} attempt(s) remaining.")
            raise ValueError("Invalid door OTP. Too many attempts — ask the customer to resend it.")

        if not self.modal.verify_door_otp(connection, booking_id, data.otp):
            # OTP was right, so the booking changed underneath us (e.g. cancelled)
            current = self.modal.read_one(connection, booking_id)
            raise ValueError(f"Cannot start a job that is {current['status']}")

        updated = self.modal.read_one(connection, booking_id)
        self._notify_status_change(connection, updated, "IN_PROGRESS")
        return "success", {"message": "OTP verified. Job started."}

    def regenerate_door_otp(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.modal.read_one(connection, booking_id)
        if str(booking.get("customer_id")) != str(user_id) and role not in ("ADMIN",):
            raise PermissionError("Access denied")
        if booking["status"] not in ("ACCEPTED", "EN_ROUTE"):
            raise ValueError("OTP can only be resent before the job has started")
        self.modal.regenerate_door_otp(connection, booking_id)
        return "success", {"message": "A new OTP has been generated — check your booking details."}

    def add_tip(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        if role != "CUSTOMER":
            raise PermissionError("Only customers can add tips")
        booking = self.modal.read_one(connection, booking_id)
        if str(booking.get("customer_id")) != str(user_id):
            raise PermissionError("Access denied")
        if booking["status"] != "COMPLETED":
            raise ValueError("Can only tip on completed bookings")
        data = AddTipSchema(**{k: v for k, v in obj.items() if k not in ("id", "_user_id", "_role")})
        tips_t = connection.execute(
            text("SELECT 1 FROM tips WHERE booking_id=:bid AND customer_id=:cid"),
            {"bid": booking_id, "cid": user_id}
        ).fetchone()
        if tips_t:
            raise ValueError("Tip already added for this booking")
        t = get_table("tips")
        connection.execute(t.insert().values(
            tip_id=new_uuid(),
            booking_id=booking_id,
            customer_id=user_id,
            provider_id=booking["provider_id"],
            amount=data.amount,
            created_at=now_utc(),
        ))
        return "success", {"message": "Tip added"}

    def rebook(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.modal.read_one(connection, booking_id)
        if str(booking.get("customer_id")) != str(user_id):
            raise PermissionError("Access denied")
        if not obj.get("scheduled_at"):
            raise ValueError("scheduled_at is required to book again")
        items = [
            {"sub_service_id": i["sub_service_id"], "quantity": i.get("quantity") or 1}
            for i in self.modal.get_items(connection, booking_id)
        ]
        new_obj = {
            "_user_id": user_id,
            "_role": role,
            "service_id": booking["service_id"],
            "scheduled_at": obj["scheduled_at"],
            "address_id": booking.get("address_id"),
            "items": items or None,
            "service_snapshot": booking.get("service_snapshot"),
            "address_snapshot": booking.get("address_snapshot"),
            "requested_provider_id": booking.get("provider_id"),
        }
        return self.create(new_obj, connection)

    def _notify_status_change(self, connection, booking, status):
        messages = {
            "ACCEPTED": ("Booking Accepted!", "Your provider has been assigned."),
            "EN_ROUTE": ("Provider En Route", "Your expert is on their way!"),
            "IN_PROGRESS": ("Service Started", "Your service is now in progress."),
            "COMPLETED": ("Service Completed", "Your service is complete. Please rate your experience."),
            "CANCELLED": ("Booking Cancelled", "Your booking has been cancelled."),
        }
        if status in messages:
            title, body = messages[status]
            try:
                self.notif.send_push(
                    connection=connection,
                    user_ids=[booking["customer_id"]],
                    title=title, body=body,
                    data={"type": "booking_update", "booking_id": booking["booking_id"], "status": status},
                )
            except Exception as e:
                print(f"[Notify] Status notification failed (non-fatal): {e}")


def _is_approved_provider(connection, user_id) -> bool:
    prov = ProvidersMaster().find_by_user_id(connection, user_id)
    return bool(prov) and prov.get("status") == "APPROVED"
