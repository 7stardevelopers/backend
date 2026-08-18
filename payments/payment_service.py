import os
import hmac
import hashlib
import razorpay

from payments.payment_modal import PaymentMaster
from payments.payment_validator import (
    CreateOrderSchema, VerifyPaymentSchema, PayoutRequestSchema, RefundSchema
)
from bookings.bookings_modal import BookingsMaster
from providers.providers_modal import ProvidersMaster
from notifications.notifications_service import NotificationsService
from utilities.common_table_elements import now_utc

PLATFORM_FEE_PCT = int(os.environ.get("PLATFORM_FEE_PCT", "10"))


def _get_razorpay_client():
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    return razorpay.Client(auth=(key_id, key_secret))


class PaymentService:
    def __init__(self):
        self.modal = PaymentMaster()
        self.booking_modal = BookingsMaster()
        self.provider_modal = ProvidersMaster()
        self.notif = NotificationsService()

    def create_order(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "CUSTOMER":
            raise PermissionError("Only customers can create payment orders")

        data = CreateOrderSchema(**obj)
        booking = self.booking_modal.read_one(connection, data.booking_id)
        if str(booking["customer_id"]) != str(user_id):
            raise PermissionError("Access denied")

        amount = booking["total_amount"]
        client = _get_razorpay_client()
        rz_order = client.order.create({
            "amount": amount,
            "currency": data.currency,
            "receipt": data.booking_id[:20],
        })

        payment = self.modal.create_payment(connection, {
            "booking_id": data.booking_id,
            "customer_id": user_id,
            "razorpay_order_id": rz_order["id"],
            "amount": amount,
            "currency": data.currency,
            "status": "PENDING",
        })

        return "success", {
            "razorpay_order_id": rz_order["id"],
            "amount": amount,
            "currency": data.currency,
            "payment_id": payment["payment_id"],
        }

    def verify_payment(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        data = VerifyPaymentSchema(**obj)

        if not _verify_signature(data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature):
            raise ValueError("Invalid payment signature")

        payment = self.modal.find_payment(connection, razorpay_order_id=data.razorpay_order_id)
        if not payment:
            raise ValueError("Payment record not found")
        if str(payment["customer_id"]) != str(user_id):
            raise PermissionError("Access denied")
        if str(payment["booking_id"]) != str(data.booking_id):
            raise ValueError("Booking does not match this payment order")

        self.modal.update_payment(connection, payment["payment_id"], {
            "razorpay_payment_id": data.razorpay_payment_id,
            "status": "PAID",
        })
        self.booking_modal.update_payment(connection, data.booking_id, payment["payment_id"], "PAID")

        booking = self.booking_modal.read_one(connection, data.booking_id)
        if booking.get("provider_id"):
            total = payment["amount"]
            fee = int(total * PLATFORM_FEE_PCT / 100)
            provider_earning = total - fee
            self.modal.add_earning(connection, booking["provider_id"], data.booking_id, provider_earning)
            self.provider_modal.update_wallet(connection, booking["provider_id"], provider_earning)

        try:
            notify_ids = [str(booking["customer_id"])]
            if booking.get("provider_id"):
                notify_ids.append(str(booking["provider_id"]))
            self.notif.send_push(
                connection=connection,
                user_ids=notify_ids,
                title="Payment Confirmed",
                body="Your payment has been received. Your booking is confirmed.",
                data={"type": "payment_confirmed", "booking_id": data.booking_id},
            )
        except Exception as e:
            print(f"[Payment] Push notification failed (non-fatal): {e}")

        return "success", {"message": "Payment verified", "payment_id": payment["payment_id"]}

    def payout_request(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        data = PayoutRequestSchema(**obj)
        provider = self.provider_modal.find_by_user_id(connection, user_id)
        if not provider:
            raise ValueError("Provider profile not found")
        if provider["wallet_balance"] < data.amount:
            raise ValueError("Insufficient wallet balance")
        payout = self.modal.create_payout_request(connection, {
            "provider_id": provider["provider_id"],
            "amount": data.amount,
            "status": "PENDING",
            "bank_account": provider.get("bank_account_number"),
            "bank_ifsc": provider.get("bank_ifsc"),
        })
        return "created", payout

    def list_all(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        status = obj.get("status")
        page = int(obj.get("page", 1))
        payments = self.modal.list_all_payments(connection, status=status, page=page)
        return "success", payments

    def request_refund(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Admin or Support role required")
        data = RefundSchema(**obj)
        booking = self.booking_modal.read_one(connection, data.booking_id)
        payment = self.modal.find_payment(connection, payment_id=booking.get("payment_id"))
        if not payment or payment["status"] != "PAID":
            raise ValueError("No paid payment found for this booking")

        client = _get_razorpay_client()
        client.payment.refund(payment["razorpay_payment_id"], {
            "amount": data.amount,
            "notes": {"reason": data.reason or "Customer refund"},
        })
        self.modal.update_payment(connection, payment["payment_id"], {
            "status": "REFUNDED",
            "refund_amount": data.amount,
        })
        return "success", {"message": "Refund initiated"}


def _verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    msg = f"{order_id}|{payment_id}"
    expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
