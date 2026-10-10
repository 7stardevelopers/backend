import hmac
import json
<<<<<<< HEAD
from datetime import datetime, timedelta, timezone
=======
from datetime import datetime, timezone
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
from sqlalchemy import text

from bookings.bookings_modal import BookingsMaster
from bookings.bookings_validator import (
    CreateBookingSchema, UpdateStatusSchema, VerifyDoorOTPSchema, AddTipSchema
)
from bookings.booking_pricing import price_booking, apply_booking_side_effects, release_booking_side_effects
from notifications.notifications_service import NotificationsService
from payments.payment_service import platform_fee_for
from payments.money_rules import CANCEL_FEE, CANCEL_GRACE_MIN, CASH_DUES_LIMIT
from providers.provider_matching import match_provider
from providers.providers_modal import ProvidersMaster, is_registered_worker, WORKER_CANNOT_BOOK
from utilities.common_table_elements import new_uuid, now_utc
from utilities.db_connection import get_table

# The code is created at the door (customer confirms the worker's face), so this
# only has to cover the wait at the door; tapping "Yes" again issues a fresh one.
DOOR_OTP_TTL_SECONDS = 4 * 3600
MAX_OTP_ATTEMPTS = 5

# PENDING → ACCEPTED goes through accept_booking (claim) and
# EN_ROUTE/ACCEPTED → IN_PROGRESS only through verify_door_otp, so neither the
# claim nor the door OTP can be bypassed via PATCH /status. IN_PROGRESS →
# COMPLETED needs BOTH complete() (worker, proof photos) and confirm_complete()
# (customer, in their own app) — in either order. Admin can still force it.
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


def door_otp_expired(booking) -> bool:
    generated_at = booking.get("door_otp_generated_at") or booking.get("created_at")
    if not generated_at:
        return False
    now = datetime.utcnow() if generated_at.tzinfo is None else datetime.now(timezone.utc)
    return (now - generated_at).total_seconds() > DOOR_OTP_TTL_SECONDS


class BookingsService:
    def __init__(self):
        self.modal = BookingsMaster()
        self.notif = NotificationsService()

    @staticmethod
    def _hide_door_otp(booking, role):
        # A provider must never be able to read the OTP directly out of a
        # response — it's the customer's spoken confirmation that the right
        # person is at the door. The customer sees it only after confirming the
        # worker's face matches their profile photo; admin always sees it.
        if role == "PROVIDER" or (role == "CUSTOMER" and not booking.get("identity_confirmed_at")):
            booking.pop("door_otp", None)
        if role == "PROVIDER":
            # Don't tip off someone the customer reported as the wrong person.
            booking.pop("identity_mismatch_at", None)
        return booking

    def create(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role == "PROVIDER" or is_registered_worker(connection, user_id):
            raise PermissionError(WORKER_CANNOT_BOOK)
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
<<<<<<< HEAD
            # Earlier cancellation fees in the total belong to that job's worker, not this one.
            "platform_fee": platform_fee_for(pricing["total_amount"] - pricing["previous_dues"]),
            "dues_collected": pricing["previous_dues"],
=======
            "platform_fee": platform_fee_for(pricing["sub_total"]),
            # Which plan quota this booking used — only that is given back on cancel.
            "subscription_id": pricing["subscription"]["subscription_id"] if pricing["subscription"] else None,
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
            "coupon_id": validated.coupon_id if pricing["coupon"] else None,
            "is_instant": validated.is_instant,
            "customer_notes": validated.customer_notes,
            "requested_provider_id": validated.requested_provider_id,
            "status": "PENDING",
            "payment_status": "PENDING",
            # Nothing to pay → nothing to wait for.
            "payment_mode": validated.payment_mode if pricing["total_amount"] > 0 else "PAY_AFTER",
        }
        booking = self.modal.create(connection, booking_data)
        apply_booking_side_effects(connection, user_id, booking["booking_id"], pricing)
        if pricing["items"]:
            self.modal.create_items(connection, booking["booking_id"], pricing["items"])

        # "Pay after job" goes to workers right away; "Pay now" waits until the
        # payment is confirmed (PaymentService.confirm_payment calls dispatch()).
        if is_dispatchable(booking):
            booking = self.dispatch(connection, booking)

        booking["coins_used"] = pricing["coins_used"]
        booking["previous_dues"] = pricing["previous_dues"]
        self._hide_door_otp(booking, role)
        return "created", booking

    def get_dues(self, obj, connection):
        """GET /bookings/dues — cancellation fees the customer's next booking will include."""
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        return "success", {"previous_dues": self.modal.due_cancel_fees(connection, user_id)}

    def dispatch(self, connection, booking):
        """Offer a paid (or ₹0) PENDING booking to workers and confirm it to the customer."""
        # "Book again" — try to directly assign the requested provider if
        # they're approved, currently online, and still offer this service.
        # Falls back to the normal broadcast-to-nearby-providers flow below.
        direct_assigned = False
        if booking.get("requested_provider_id"):
            try:
                prov = ProvidersMaster().find_by_id(connection, booking.get("requested_provider_id"))
                if prov and prov.get("status") == "APPROVED" and prov.get("is_available"):
                    ps_t = get_table("provider_services")
                    offers_service = connection.execute(
                        ps_t.select()
                        .where(ps_t.c.provider_id == booking.get("requested_provider_id"))
                        .where(ps_t.c.service_id == booking["service_id"])
                    ).fetchone()
                    if offers_service:
                        direct_assigned = self.modal.claim_booking(
                            connection, booking["booking_id"], booking.get("requested_provider_id")
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
                if isinstance(addr, str):  # re-read from the DB (verify_payment path)
                    addr = json.loads(addr)
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
            user_ids=[booking["customer_id"]],
            title="Booking confirmed!",
            body="We're finding the best expert for you.",
            data={"type": "booking_confirmed", "booking_id": booking["booking_id"]},
        )
        return booking

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
        if role == "CUSTOMER":
            self.modal.attach_provider_summary(connection, bookings)
        for b in bookings:
            self._hide_door_otp(b, role)
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
            elif role == "PROVIDER" and booking.get("status") == "PENDING" and is_dispatchable(booking) \
                    and _is_approved_provider(connection, user_id):
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
                    SELECT u.name, u.photo_url, p.avg_rating, p.total_reviews,
                           p.years_experience, p.status,
                           (SELECT COUNT(*) FROM bookings cb
                            WHERE cb.provider_id = p.provider_id AND cb.status = 'COMPLETED') AS total_jobs
                    FROM providers p
                    JOIN users u ON u.user_id = p.user_id
                    WHERE p.provider_id = :pid
                """), {"pid": booking["provider_id"]}).fetchone()
                if row:
                    booking["provider_name"]  = row.name
                    booking["provider_photo"] = row.photo_url
                    booking["provider_rating"] = float(row.avg_rating or 0)
                    # Identity line on the tracking screen (Rapido shows the vehicle number here).
                    booking["provider_years_experience"] = int(row.years_experience or 0)
                    booking["provider_total_jobs"] = int(row.total_jobs or 0)
                    booking["provider_verified"] = row.status == "APPROVED"
                # Providers always see their own location; customers only while the booking is live.
                from bookings.live_tracking import is_live_tracking, provider_busy_elsewhere
                busy = provider_busy_elsewhere(connection, booking)
                booking["provider_busy"] = busy
                show_loc = role in ("ADMIN", "SUPPORT", "PROVIDER") or (is_live_tracking(booking) and not busy)
                loc = ProvidersMaster().get_location(connection, booking["provider_id"]) if show_loc else None
                if loc:
                    booking["provider_lat"] = float(loc["lat"]) if loc.get("lat") else None
                    booking["provider_lng"] = float(loc["lng"]) if loc.get("lng") else None
                    booking["provider_location_updated_at"] = loc.get("updated_at")
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
        if role == "CUSTOMER":
            # Lets the app warn before a late cancel ("A ₹50 fee applies").
            booking["cancel_fee_if_cancelled"] = cancellation_fee_for(booking)
            booking["cancel_free_until"] = cancel_free_until(booking)
            booking["cancel_fee_amount"] = CANCEL_FEE  # charged once cancel_free_until has passed
        self._hide_door_otp(booking, role)
        return "success", booking

    def _trip_endpoints(self, obj, connection):
        """(booking_id, origin, dest) for the provider → booking address leg, or
        None when either point is unknown or the customer may not see it."""
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.modal.read_one(connection, booking_id)
        if not booking or not booking.get("provider_id"):
            raise ValueError("No expert assigned yet")
        is_customer = False
        if role not in ("ADMIN", "SUPPORT"):
            is_customer = str(booking.get("customer_id")) == str(user_id)
            prov = ProvidersMaster().find_by_user_id(connection, user_id) if not is_customer else None
            is_provider = bool(prov) and str(prov["provider_id"]) == str(booking["provider_id"])
            if not is_customer and not is_provider:
                raise PermissionError("Access denied")
        from bookings.live_tracking import is_live_tracking
        if is_customer and not is_live_tracking(booking, conn=connection):
            return None

        addr = booking.get("address_snapshot") or {}
        if isinstance(addr, str):
            import json
            try:
                addr = json.loads(addr)
            except ValueError:
                addr = {}
        loc = ProvidersMaster().get_location(connection, booking["provider_id"])
        if not loc or loc.get("lat") is None or addr.get("lat") is None or addr.get("lng") is None:
            return None
        return booking_id, (float(loc["lat"]), float(loc["lng"])), (float(addr["lat"]), float(addr["lng"]))

    @staticmethod
    def _redis_or_none():
        from utilities.redis_connection import get_redis
        try:
            return get_redis()
        except Exception:
            return None

    def get_eta(self, obj, connection):
        """Road distance/ETA from the assigned provider to the booking address —
        one shared number for the customer and the worker apps."""
        trip = self._trip_endpoints(obj, connection)
        if trip is None:
            return "success", None
        from bookings.booking_eta import road_eta
        return "success", road_eta(*trip, self._redis_or_none())

    def get_route(self, obj, connection):
        """Encoded road polyline + distance/ETA for the customer's live map."""
        trip = self._trip_endpoints(obj, connection)
        if trip is None:
            return "success", None
        from bookings.booking_eta import road_route
        return "success", road_route(*trip, self._redis_or_none())

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
        if is_blocked_for_dues(provider):
            return "success", []  # the app shows "clear your dues" from /providers/me/earnings

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
        if is_blocked_for_dues(provider):
            raise PermissionError(f"You owe ₹{-int(provider.get('wallet_balance') or 0) // 100} from cash jobs — "
                                  "clear your dues in Earnings to get new jobs")
        target = self.modal.read_one(connection, booking_id)
        if target and str(target.get("customer_id")) == str(user_id):
            raise PermissionError("You can't accept your own booking")
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
            fee = cancellation_fee_for(booking) if role == "CUSTOMER" else 0
            updated = self._do_cancel(connection, booking, fee=fee)
            self._hide_door_otp(updated, role)
            return "success", updated
        updated = self.modal.update_status(connection, booking_id, new_status, expected_status=booking["status"])
        if new_status == "COMPLETED":  # admin force-complete
            from payments.payment_service import PaymentService
<<<<<<< HEAD
            PaymentService().settle_completed_booking(connection, booking_id)
=======
            PaymentService().credit_provider_for_booking(connection, booking_id)
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
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
        if booking.get("provider_done_at"):
            raise ValueError("You've already marked this job done — waiting for the customer to confirm")
        if proof_photos:
            self.modal.update_proof_photos(connection, booking_id, proof_photos)
        if not self.modal.mark_done(connection, booking_id, "provider"):
            raise ValueError("Booking status changed — please refresh and try again")
        if self.modal.try_finish(connection, booking_id):
            self._on_completed(connection, booking_id)
            return "success", {"message": "Booking completed", "status": "COMPLETED"}
        # The customer still has to confirm in their own app — never a code the
        # worker could ask for and type in.
        body = "Please check the work and tap \"Work done\" to confirm."
        if booking.get("payment_status") != "PAID" and int(booking.get("total_amount") or 0) > 0:
            body = ("Please check the work, pay by cash to your expert or online in the app, "
                    "then tap \"Work done\".")
        self._push(connection, [booking["customer_id"]],
                   "Your expert marked the job done", body,
                   {"type": "completion_requested", "booking_id": booking_id})
        return "success", {"message": "Waiting for the customer to confirm",
                           "status": "IN_PROGRESS", "waiting_for": "customer"}

    def confirm_complete(self, obj, connection):
        """Customer's half of completion: POST /bookings/{id}/confirm-complete."""
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.modal.read_one(connection, booking_id)
        if str(booking.get("customer_id")) != str(user_id):
            raise PermissionError("Access denied")
        if booking["status"] != "IN_PROGRESS":
            raise ValueError("You can confirm only while the job is in progress")
        if booking.get("completion_disputed_at"):
            raise ValueError("You reported a problem with this job — support will contact you")
        if booking.get("customer_done_at"):
            raise ValueError("You've already confirmed — waiting for your expert to finish")
        if not self.modal.mark_done(connection, booking_id, "customer"):
            raise ValueError("Booking status changed — please refresh and try again")
        if self.modal.try_finish(connection, booking_id):
            self._on_completed(connection, booking_id)
            return "success", {"message": "Booking completed", "status": "COMPLETED"}
        self._push(connection, [self._provider_user_id(connection, booking)],
                   "Customer confirmed the job is done",
                   "Tap \"Mark as Complete\" to finish the job.",
                   {"type": "completion_confirmed", "booking_id": booking_id})
        return "success", {"message": "Waiting for your expert to finish",
                           "status": "IN_PROGRESS", "waiting_for": "provider"}

    def report_problem(self, obj, connection):
        """Customer says the work isn't done: POST /bookings/{id}/report-problem.
        Blocks completion and opens a support ticket for the booking."""
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        message = str(obj.get("message") or "").strip()
        if not message:
            raise ValueError("Please describe the problem")
        if len(message) > 1000:
            raise ValueError("Please keep it under 1000 characters")
        booking = self.modal.read_one(connection, booking_id)
        if str(booking.get("customer_id")) != str(user_id):
            raise PermissionError("Access denied")
        if booking["status"] != "IN_PROGRESS":
            raise ValueError("You can report a problem only while the job is in progress")
        if not self.modal.mark_disputed(connection, booking_id):
            raise ValueError("A problem is already reported for this booking — support will contact you")

        from support.support_service import SupportService
        support = SupportService()
        _, ticket = support.create_ticket({
            "_user_id": user_id, "_role": role,
            "subject": f"Job not completed: {message}"[:200],
            "category": "SERVICE_ISSUE", "booking_id": booking_id, "priority": "HIGH",
        }, connection)
        support.modal.add_message(connection, ticket["ticket_id"], user_id, message)

        self._push(connection, [self._provider_user_id(connection, booking)],
                   "Customer reported a problem",
                   "The customer says the job isn't finished. Support will contact you.",
                   {"type": "completion_disputed", "booking_id": booking_id})
        return "success", {"message": "Problem reported — support will contact you",
                           "ticket_id": ticket["ticket_id"]}

    def _on_completed(self, connection, booking_id):
        from payments.payment_service import PaymentService
        PaymentService().settle_completed_booking(connection, booking_id)
        booking = self.modal.read_one(connection, booking_id)
        self._notify_status_change(connection, booking, "COMPLETED")
        self._push(connection, [self._provider_user_id(connection, booking)],
                   "Job completed", "Both sides confirmed. Your earnings are updated.",
                   {"type": "booking_update", "booking_id": booking_id, "status": "COMPLETED"})

    @staticmethod
    def _provider_user_id(connection, booking):
        prov = ProvidersMaster().find_by_id(connection, booking.get("provider_id")) if booking.get("provider_id") else None
        return str(prov["user_id"]) if prov else None

    def _push(self, connection, user_ids, title, body, data):
        user_ids = [str(u) for u in user_ids if u]
        if not user_ids:
            return
        try:
            self.notif.send_push(connection=connection, user_ids=user_ids, title=title, body=body, data=data)
        except Exception as e:
            print(f"[Notify] Completion notification failed (non-fatal): {e}")

    def cancel(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        booking = self.modal.read_one(connection, booking_id)
        if str(booking.get("customer_id")) != str(user_id) and role not in ("ADMIN",):
            raise PermissionError("Access denied")
        if booking["status"] not in ("PENDING", "ACCEPTED"):
            raise ValueError(f"Cannot cancel booking in {booking['status']} status")
        # Rapido-style: free until a worker accepts + a short grace period; admin cancels are free.
        fee = cancellation_fee_for(booking) if str(booking.get("customer_id")) == str(user_id) else 0
        updated = self._do_cancel(connection, booking, fee=fee)
        updated["cancellation_fee"] = fee
        return "success", updated

<<<<<<< HEAD
    def _do_cancel(self, connection, booking, fee: int = 0):
=======
    def _do_cancel(self, connection, booking, only_unpaid=False):
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
        booking_id = booking["booking_id"]
        updated = self.modal.update_status(
            connection, booking_id, "CANCELLED", expected_status=booking["status"], only_unpaid=only_unpaid
        )
        release_booking_side_effects(connection, booking)
        self._notify_status_change(connection, updated, "CANCELLED")
        if booking.get("provider_id"):
            try:
                prov = ProvidersMaster().find_by_id(connection, booking["provider_id"])
                if prov:
                    body = "A booking assigned to you has been cancelled."
                    if fee:
                        body = f"The customer cancelled late — you'll receive ₹{fee // 100} as a cancellation fee."
                    self.notif.send_push(
                        connection=connection,
                        user_ids=[prov["user_id"]],
                        title="Booking Cancelled",
                        body=body,
                        data={"type": "booking_update", "booking_id": booking_id, "status": "CANCELLED"},
                    )
            except Exception as e:
                print(f"[Cancel] Provider notification failed (non-fatal): {e}")

        # Refund an online payment and take back anything already credited to the worker.
        # Re-read: a payment may have landed between our read and the cancel.
        from payments.payment_service import PaymentService
        pay = PaymentService()
        fresh = self.modal.read_one(connection, booking_id)
        if fee and fresh.get("provider_id"):
            if fresh.get("payment_status") in ("PAID", "PARTIALLY_REFUNDED"):
                # Paid online: keep the fee out of the refund and pay the worker now.
                kept = pay.refund_booking_on_cancel(connection, fresh, keep=fee)
                if kept:
                    pay.credit_cancel_fee(connection, fresh["provider_id"], booking_id, kept)
                    self.modal.set_cancel_fee(connection, booking_id, kept, "COLLECTED")
            else:
                # Unpaid: the customer's next booking carries it (booking_pricing.previous_dues).
                pay.refund_booking_on_cancel(connection, fresh)
                self.modal.set_cancel_fee(connection, booking_id, fee, "DUE")
            updated = self.modal.read_one(connection, booking_id)
        else:
            pay.refund_booking_on_cancel(connection, fresh)

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

        if not booking.get("door_otp"):
            raise ValueError("The customer hasn't confirmed it's you yet. Ask them to check your photo in their app.")

        if (booking.get("otp_attempt_count") or 0) >= MAX_OTP_ATTEMPTS:
            raise ValueError("Too many incorrect attempts. Ask the customer to resend the OTP.")

        if door_otp_expired(booking):
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
        if role != "ADMIN" and not booking.get("identity_confirmed_at"):
            raise ValueError("Confirm the expert at your door first")
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


def _as_utc(ts):
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def cancel_free_until(booking):
    """ISO time until which the customer may cancel for free (None = no grace clock running)."""
    accepted = _as_utc(booking.get("accepted_at"))
    if booking.get("status") != "ACCEPTED" or not accepted:
        return None
    return (accepted + timedelta(minutes=CANCEL_GRACE_MIN)).isoformat().replace("+00:00", "Z")


def cancellation_fee_for(booking) -> int:
    """Fee for the customer cancelling now: free while an expert is being found
    and for CANCEL_GRACE_MIN after one accepts, then CANCEL_FEE."""
    accepted = _as_utc(booking.get("accepted_at"))
    if booking.get("status") != "ACCEPTED" or not accepted or not booking.get("provider_id"):
        return 0
    if datetime.now(timezone.utc) - accepted <= timedelta(minutes=CANCEL_GRACE_MIN):
        return 0
    return CANCEL_FEE


def is_blocked_for_dues(provider) -> bool:
    """Worker owes CASH_DUES_LIMIT or more (fees on cash jobs): no new jobs until paid."""
    return int(provider.get("wallet_balance") or 0) <= -CASH_DUES_LIMIT


def is_dispatchable(booking) -> bool:
    """May workers see this PENDING booking? "Pay after job" always; "Pay now" once paid."""
    return (booking.get("payment_mode") != "PAY_NOW"
            or booking.get("payment_status") == "PAID"
            or int(booking.get("total_amount") or 0) == 0)


def _is_approved_provider(connection, user_id) -> bool:
    prov = ProvidersMaster().find_by_user_id(connection, user_id)
    return bool(prov) and prov.get("status") == "APPROVED"
