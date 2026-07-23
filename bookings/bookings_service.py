import os
import razorpay
from sqlalchemy import text

from bookings.bookings_modal import BookingsMaster
from bookings.bookings_validator import (
    CreateBookingSchema, UpdateStatusSchema, VerifyDoorOTPSchema, AddTipSchema
)
from coupons.coupons_modal import CouponsMaster
from notifications.notifications_service import NotificationsService
from payments.payment_modal import PaymentMaster
from providers.provider_matching import match_provider
from providers.providers_modal import ProvidersMaster
from subscriptions.subscriptions_modal import SubscriptionsMaster
from utilities.common_table_elements import new_uuid, now_utc
from utilities.db_connection import get_table

ALLOWED_TRANSITIONS = {
    "PROVIDER": {
        "PENDING":     ["ACCEPTED", "REJECTED"],
        "ACCEPTED":    ["EN_ROUTE"],
        "EN_ROUTE":    ["IN_PROGRESS"],
        "IN_PROGRESS": ["COMPLETED"],
    },
    "CUSTOMER": {
        "PENDING":  ["CANCELLED"],
        "ACCEPTED": ["CANCELLED"],
    },
    "ADMIN": {
        "PENDING":  ["CANCELLED", "ACCEPTED"],
        "ACCEPTED": ["CANCELLED"],
        "IN_PROGRESS": ["COMPLETED", "CANCELLED"],
    },
}


class BookingsService:
    def __init__(self):
        self.modal = BookingsMaster()
        self.notif = NotificationsService()

    def create(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role not in ("CUSTOMER",):
            raise PermissionError("Only customers can create bookings")

        validated = CreateBookingSchema(**obj)
        sub_total = validated.sub_total
        discount = validated.discount

        sub_modal = SubscriptionsMaster()
        active_sub = sub_modal.get_active_subscription(connection, user_id)
        if active_sub:
            plan = sub_modal.get_plan(connection, active_sub["plan_id"])
            if plan and plan.get("discount_pct"):
                sub_discount = int(sub_total * plan["discount_pct"] / 100)
                discount += sub_discount

        total_amount = max(0, sub_total - discount)

        booking_data = {
            "customer_id": user_id,
            "service_id": validated.service_id,
            "scheduled_at": validated.scheduled_at,
            "address_id": validated.address_id,
            "address_snapshot": validated.address_snapshot,
            "service_snapshot": validated.service_snapshot,
            "sub_total": sub_total,
            "discount": discount,
            "total_amount": total_amount,
            "coupon_id": validated.coupon_id,
            "is_instant": validated.is_instant,
            "customer_notes": validated.customer_notes,
            "status": "PENDING",
            "payment_status": "PENDING",
        }
        booking = self.modal.create(connection, booking_data)

        if active_sub:
            try:
                sub_modal.increment_bookings_used(connection, user_id)
            except Exception as e:
                print(f"[Subscription] increment_bookings_used failed (non-fatal): {e}")

        if validated.coupon_id:
            try:
                CouponsMaster().record_use(connection, validated.coupon_id, user_id, booking["booking_id"])
            except Exception as e:
                print(f"[Coupon] record_use failed (non-fatal): {e}")

        if validated.items:
            self.modal.create_items(connection, booking["booking_id"], validated.items)

        try:
            provider = match_provider(connection, booking)
            if provider:
                self.modal.assign_provider(connection, booking["booking_id"], provider["provider_id"])
                booking["provider_id"] = provider["provider_id"]
                booking["status"] = "ACCEPTED"
                self.notif.send_push(
                    connection=connection,
                    user_ids=[provider["user_id"]],
                    title="New job request",
                    body=f"New booking assigned to you",
                    data={"type": "job_request", "booking_id": booking["booking_id"]},
                )
        except Exception as e:
            print(f"[Matching] non-fatal: {e}")

        self.notif.send_push(
            connection=connection,
            user_ids=[user_id],
            title="Booking confirmed!",
            body="We're finding the best expert for you.",
            data={"type": "booking_confirmed", "booking_id": booking["booking_id"]},
        )
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
            filters["provider_id"] = user_id
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
        if role not in ("ADMIN", "SUPPORT") and str(booking.get("customer_id")) != str(user_id) and str(booking.get("provider_id")) != str(user_id):
            raise PermissionError("Access denied")
        booking["items"] = self.modal.get_items(connection, booking_id)
        # Attach provider's live location so the customer tracking screen has an initial position
        if booking.get("provider_id"):
            try:
                loc = ProvidersMaster().get_location(connection, booking["provider_id"])
                if loc:
                    booking["provider_lat"] = float(loc["lat"]) if loc.get("lat") else None
                    booking["provider_lng"] = float(loc["lng"]) if loc.get("lng") else None
            except Exception:
                pass
        return "success", booking

    def list_available_for_provider(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        prov_master = ProvidersMaster()
        provider = prov_master.find_by_user_id(connection, user_id)
        if not provider:
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
        return "success", booking

    def update_status(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        new_status = obj.get("status")
        booking = self.modal.read_one(connection, booking_id)

        if role == "PROVIDER" and str(booking.get("provider_id")) != str(user_id):
            raise PermissionError("You are not assigned to this booking")

        allowed = ALLOWED_TRANSITIONS.get(role, {}).get(booking["status"], [])
        if new_status not in allowed:
            raise ValueError(f"Cannot transition from {booking['status']} to {new_status}")
        updated = self.modal.update_status(connection, booking_id, new_status)
        self._notify_status_change(connection, updated, new_status)
        return "success", updated

    def complete(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        proof_photos = obj.get("proof_photos", [])
        booking = self.modal.read_one(connection, booking_id)
        if role != "PROVIDER" or str(booking.get("provider_id")) != str(user_id):
            raise PermissionError("You are not assigned to this booking")
        if booking["status"] != "IN_PROGRESS":
            raise ValueError("Booking must be IN_PROGRESS to complete")
        if proof_photos:
            self.modal.update_proof_photos(connection, booking_id, proof_photos)
        updated = self.modal.update_status(connection, booking_id, "COMPLETED")
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
        updated = self.modal.update_status(connection, booking_id, "CANCELLED")

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

        return "success", updated

    def verify_door_otp(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        if role != "PROVIDER":
            raise PermissionError("Only providers can verify door OTP")
        data = VerifyDoorOTPSchema(**{k: v for k, v in obj.items() if not k.startswith("_") and k not in ("id",)})
        verified = self.modal.verify_door_otp(connection, booking_id, data.otp)
        if not verified:
            raise ValueError("Invalid door OTP")
        self.modal.update_status(connection, booking_id, "IN_PROGRESS")
        return "success", {"message": "OTP verified. Job started."}

    def add_tip(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        if role != "CUSTOMER":
            raise PermissionError("Only customers can add tips")
        booking = self.modal.read_one(connection, booking_id)
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
        new_obj = {
            "_user_id": user_id,
            "_role": role,
            "service_id": booking["service_id"],
            "scheduled_at": obj.get("scheduled_at", booking["scheduled_at"]),
            "address_id": booking.get("address_id"),
            "sub_total": booking["sub_total"],
            "discount": 0,
            "total_amount": booking["sub_total"],
            "service_snapshot": booking.get("service_snapshot"),
            "address_snapshot": booking.get("address_snapshot"),
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
