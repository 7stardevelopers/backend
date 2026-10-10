import os
import hmac
import hashlib
import json
import razorpay

from payments.payment_modal import PaymentMaster
from payments.payment_validator import (
    CreateOrderSchema, VerifyPaymentSchema, PayoutRequestSchema, RefundSchema
)
from bookings.bookings_modal import BookingsMaster
from providers.providers_modal import ProvidersMaster, is_registered_worker, WORKER_CANNOT_BOOK
from notifications.notifications_service import NotificationsService

# All amounts are integer paise (₹499 = 49900) — the unit Razorpay takes.
PLATFORM_FEE_PCT = int(os.environ.get("PLATFORM_FEE_PCT", "10"))
CURRENCY = "INR"
UNPAYABLE_BOOKING_STATUSES = ("CANCELLED", "REJECTED")


class PaymentsNotConfigured(RuntimeError):
    pass


def _secret(name: str) -> str:
    """Fail closed: with an empty key the HMAC checks below would accept
    signatures anyone can compute."""
    value = os.environ.get(name, "")
    if not value:
        raise PaymentsNotConfigured(f"Payments are not configured ({name} missing)")
    return value


def _get_razorpay_client():
    return razorpay.Client(auth=(_secret("RAZORPAY_KEY_ID"), _secret("RAZORPAY_KEY_SECRET")))


def platform_fee_for(total_amount: int) -> int:
    return int(int(total_amount or 0) * PLATFORM_FEE_PCT / 100)


class PaymentService:
    def __init__(self):
        self.modal = PaymentMaster()
        self.booking_modal = BookingsMaster()
        self.provider_modal = ProvidersMaster()
        self.notif = NotificationsService()

    # ── checkout ─────────────────────────────────────────────────────────────

    def create_order(self, obj, connection):
        """POST /payments/create-order {booking_id} or {plan_id}. Amount always from the DB."""
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role == "PROVIDER" or is_registered_worker(connection, user_id):
            raise PermissionError(WORKER_CANNOT_BOOK)
        if role != "CUSTOMER":
            raise PermissionError("Only customers can create payment orders")
        data = CreateOrderSchema(**obj)

        if data.booking_id:
            booking = self.booking_modal.read_one(connection, data.booking_id)
            if str(booking["customer_id"]) != str(user_id):
                raise PermissionError("Access denied")
            if booking["status"] in UNPAYABLE_BOOKING_STATUSES:
                raise ValueError("This booking is cancelled and can't be paid")
            if booking["status"] == "COMPLETED":
                # Unpaid jobs are settled in cash at the door when the work is done.
                raise ValueError("This job is completed — it was settled with your expert")
            if booking.get("payment_status") in ("PAID", "REFUNDED", "PARTIALLY_REFUNDED"):
                raise ValueError("This booking is already paid")
            amount = int(booking["total_amount"] or 0)
            target = {"booking_id": data.booking_id, "purpose": "BOOKING"}
            receipt = data.booking_id
        else:
            from subscriptions.subscriptions_modal import SubscriptionsMaster
            subs = SubscriptionsMaster()
            plan = subs.get_plan(connection, data.plan_id)
            if not plan or not plan.get("is_active", True):
                raise ValueError("Plan not found")
            active = subs.get_active_subscription(connection, user_id)
            if active and str(active["plan_id"]) == str(data.plan_id):
                raise ValueError("This plan is already active")
            amount = int(plan.get("price") or 0)
            target = {"plan_id": data.plan_id, "purpose": "SUBSCRIPTION"}
            receipt = f"plan-{data.plan_id}"
        if amount <= 0:
            raise ValueError("Nothing to pay")

        existing = self.modal.find_open_order(connection, user_id, amount,
                                              booking_id=target.get("booking_id"), plan_id=target.get("plan_id"))
        if existing:
            payment = existing
        else:
            rz_order = _get_razorpay_client().order.create({
                "amount": amount,
                "currency": CURRENCY,
                "receipt": receipt[:40],
                "notes": {k: v for k, v in target.items()},
            })
            payment = self.modal.create_payment(connection, {
                **target,
                "customer_id": user_id,
                "razorpay_order_id": rz_order["id"],
                "amount": amount,
                "currency": CURRENCY,
                "status": "PENDING",
            })
        return "success", {
            "razorpay_order_id": payment["razorpay_order_id"],
            "amount": amount,
            "currency": CURRENCY,
            "payment_id": payment["payment_id"],
            # The app opens Checkout with this key, so test/live always matches the
            # secret the order was created with (switch = change the backend secret).
            "key_id": _secret("RAZORPAY_KEY_ID"),
        }

    def verify_payment(self, obj, connection):
        """POST /payments/verify — the app's success callback. Safe to call twice."""
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        data = VerifyPaymentSchema(**obj)

        if not _verify_signature(data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature):
            raise ValueError("Invalid payment signature")

        payment = self.modal.find_payment(connection, razorpay_order_id=data.razorpay_order_id)
        if not payment:
            raise ValueError("Payment record not found")
        if str(payment["customer_id"]) != str(user_id):
            raise PermissionError("Access denied")
        if data.booking_id and str(payment.get("booking_id")) != str(data.booking_id):
            raise ValueError("Booking does not match this payment order")
        if data.plan_id and str(payment.get("plan_id")) != str(data.plan_id):
            raise ValueError("Plan does not match this payment order")

        if self._is_extra_payment(payment, data.razorpay_payment_id):
            self._release_extra_payment(payment, data.razorpay_payment_id)
            return "success", {"message": "This was already paid — the extra payment is being refunded.",
                               "payment_id": payment["payment_id"], "refunded": True}
        method = self._capture_if_needed(payment, data.razorpay_payment_id)
        outcome = self.confirm_payment(connection, payment, data.razorpay_payment_id, method)
        if outcome == "duplicate_refunded":
            return "success", {"message": "This was already paid — the extra payment is being refunded.",
                               "payment_id": payment["payment_id"], "refunded": True}
        return "success", {"message": "Payment verified", "payment_id": payment["payment_id"]}

    @staticmethod
    def _is_extra_payment(payment, razorpay_payment_id) -> bool:
        """Order already settled by a different Razorpay payment (e.g. a late UPI retry)."""
        return (payment.get("status") not in ("PENDING", "FAILED")
                and payment.get("razorpay_payment_id")
                and payment["razorpay_payment_id"] != razorpay_payment_id)

    def _release_extra_payment(self, payment, razorpay_payment_id):
        """Never keep it: an authorized one is left uncaptured (Razorpay voids it),
        a captured one is refunded in full."""
        try:
            client = _get_razorpay_client()
            rp = client.payment.fetch(razorpay_payment_id)
            if rp.get("status") == "captured":
                client.payment.refund(razorpay_payment_id, {
                    "amount": int(rp.get("amount") or 0),
                    "notes": {"reason": "Duplicate payment on an already-paid order"},
                })
            print(f"[Payment] extra payment {razorpay_payment_id} on {payment['payment_id']} released "
                  f"({rp.get('status')})")
        except Exception as e:
            print(f"[Payment] COULD NOT release extra payment {razorpay_payment_id} on "
                  f"{payment['payment_id']} — refund it from the Razorpay dashboard: {e}")

    def _capture_if_needed(self, payment, razorpay_payment_id):
        """Payments stay 'authorized' (and auto-refund after a few days) unless
        auto-capture is on in the dashboard — capture them here regardless.
        Returns the payment method (upi/card/…) when Razorpay tells us."""
        try:
            client = _get_razorpay_client()
            rp = client.payment.fetch(razorpay_payment_id)
            if int(rp.get("amount") or 0) != int(payment["amount"]):
                raise ValueError("Paid amount does not match the order")
            if rp.get("status") == "authorized":
                client.payment.capture(razorpay_payment_id, int(payment["amount"]), {"currency": CURRENCY})
            return rp.get("method")
        except ValueError:
            raise
        except Exception as e:
            # The signature already proved the payment; the webhook will settle the rest.
            print(f"[Payment] fetch/capture failed for {razorpay_payment_id} (non-fatal): {e}")
            return None

    def confirm_payment(self, connection, payment, razorpay_payment_id, method=None) -> str:
        """Shared by verify + webhook. Returns 'paid' | 'already' | 'duplicate_refunded'."""
        if not self.modal.mark_paid(connection, payment["payment_id"], razorpay_payment_id, method):
            return "already"
        payment = self.modal.find_payment(connection, payment_id=payment["payment_id"])

        if payment.get("purpose") == "WORKER_DUES":
            provider = self.provider_modal.find_by_user_id(connection, payment["customer_id"])
            if provider:
                self.modal.add_earning(connection, provider["provider_id"], None, int(payment["amount"]), "DUES_PAID")
                self.provider_modal.update_wallet(connection, provider["provider_id"], int(payment["amount"]))
            self._push(connection, [payment["customer_id"]], "Dues cleared",
                       "Thanks! Your dues are paid — you can take new jobs again.",
                       {"type": "dues_paid"})
            return "paid"

        if payment.get("purpose") == "SUBSCRIPTION":
            from subscriptions.subscriptions_modal import SubscriptionsMaster
            subs = SubscriptionsMaster()
            active = subs.get_active_subscription(connection, payment["customer_id"])
            if active and str(active["plan_id"]) == str(payment["plan_id"]) \
                    and str(active.get("payment_id")) != str(payment["payment_id"]):
                # Same plan already bought with another payment (retry / late UPI) — give this back.
                self.refund(connection, payment, None, "Plan already active", raise_on_error=False)
                return "duplicate_refunded"
            subs.create_subscription(connection, payment["customer_id"], payment["plan_id"], payment["payment_id"])
            self._push(connection, [payment["customer_id"]], "Subscription active",
                       "Your plan is active. Enjoy your discount on bookings.",
                       {"type": "subscription_active"})
            return "paid"

        booking_id = payment["booking_id"]
        if not self.booking_modal.mark_paid(connection, booking_id, payment["payment_id"]):
            # Paid twice (two orders / two devices) or the booking was cancelled
            # meanwhile — give this payment straight back.
            self.refund(connection, payment, None, "Duplicate payment or cancelled booking", raise_on_error=False)
            return "duplicate_refunded"

        booking = self.booking_modal.read_one(connection, booking_id)
<<<<<<< HEAD
        if booking["status"] == "PENDING" and not booking.get("provider_id") \
                and booking.get("payment_mode") == "PAY_NOW":
            # "Pay now" bookings are held back from workers until this moment.
            # mark_paid above only succeeds once, so verify + webhook dispatch once.
=======
        if booking["status"] == "PENDING" and not booking.get("provider_id"):
            # Pay-first: only now does the job reach workers (list + "New Job" push).
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
            from bookings.bookings_service import BookingsService
            try:
                BookingsService().dispatch(connection, booking)
            except Exception as e:
                print(f"[Payment] Dispatch after payment failed (non-fatal): {e}")
<<<<<<< HEAD
=======
            booking = self.booking_modal.read_one(connection, booking_id)  # may be assigned now
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
        self.credit_provider_for_booking(connection, booking_id)
        provider_user = self._provider_user_id(connection, booking)
        self._push(connection, [booking["customer_id"]], "Payment Confirmed",
                   "Your payment has been received. Your booking is confirmed.",
                   {"type": "payment_confirmed", "booking_id": booking_id})
        self._push(connection, [provider_user], "Customer paid online",
                   "This job is paid online. Your share is added to your wallet when the job is completed.",
                   {"type": "payment_confirmed", "booking_id": booking_id})
        return "paid"

    # ── worker earnings ──────────────────────────────────────────────────────

    def settle_completed_booking(self, connection, booking_id):
        """Every completion path (both sides done, admin force-complete) ends here.
        Online-paid → the worker's share goes to their wallet. Unpaid → the worker
        took cash, so the platform fee becomes dues on their wallet (Rapido-style).
        Late-cancel fees this booking carried go to the workers they belong to."""
        for row in self.booking_modal.collect_cancel_fees(connection, booking_id):
            if row.get("provider_id"):
                self.credit_cancel_fee(connection, row["provider_id"], row["booking_id"],
                                       int(row["cancellation_fee"] or 0))
        booking = self.booking_modal.read_one(connection, booking_id)
        if booking.get("payment_status") in ("PAID", "PARTIALLY_REFUNDED"):
            self.credit_provider_for_booking(connection, booking_id)
        else:
            self.charge_cash_fee(connection, booking)

    def charge_cash_fee(self, connection, booking) -> int:
        """Cash job done: the worker holds all the money, so they owe the platform
        fee (plus any earlier cancellation fee the bill carried for another worker)."""
        booking_id = booking["booking_id"]
        total = int(booking.get("total_amount") or 0)
        if (booking.get("status") != "COMPLETED" or not booking.get("provider_id") or total <= 0
                or booking.get("payment_status") in ("PAID", "PARTIALLY_REFUNDED", "REFUNDED")
                or self.modal.has_entry(connection, booking_id, "CASH_FEE")):
            return 0
        dues = int(booking.get("dues_collected") or 0)
        fee = (int(booking.get("platform_fee") or 0) or platform_fee_for(total - dues)) + dues
        if fee <= 0:
            return 0
        provider_id = booking["provider_id"]
        self.modal.add_earning(connection, provider_id, booking_id, fee, "CASH_FEE")
        self.provider_modal.update_wallet(connection, provider_id, fee, "debit")
        prov = self.provider_modal.find_by_id(connection, provider_id)
        from payments.money_rules import CASH_DUES_LIMIT
        if prov and int(prov.get("wallet_balance") or 0) <= -CASH_DUES_LIMIT:
            self._push(connection, [prov["user_id"]], "Clear your dues to get new jobs",
                       f"You owe ₹{-int(prov['wallet_balance']) // 100} in fees from cash jobs. "
                       "Pay it from Earnings to start getting jobs again.",
                       {"type": "dues_limit"})
        return fee

    def credit_cancel_fee(self, connection, provider_id, booking_id, amount) -> int:
        """The customer cancelled late: the cancellation fee is the worker's."""
        if amount <= 0 or self.modal.has_entry(connection, booking_id, "CANCEL_FEE"):
            return 0
        self.modal.add_earning(connection, provider_id, booking_id, amount, "CANCEL_FEE")
        self.provider_modal.update_wallet(connection, provider_id, amount)
        return amount

    def credit_provider_for_booking(self, connection, booking_id) -> int:
        """Credit the worker's share once the job is COMPLETED and paid online —
        whichever happens last calls this. Returns the amount credited (0 = not yet / already)."""
        if not self.booking_modal.claim_earning_credit(connection, booking_id):
            return 0
        booking = self.booking_modal.read_one(connection, booking_id)
<<<<<<< HEAD
        if (self.modal.has_entry(connection, booking_id, "CASH_FEE")
                and not self.modal.has_entry(connection, booking_id, "CASH_FEE_REVERSAL")):
            # Settled as cash at completion, then the online payment landed after all:
            # the worker never held the cash, so give the cash fee back.
            cash_fee = int(self.modal.earnings_total(connection, booking_id, "CASH_FEE"))
            self.modal.add_earning(connection, booking["provider_id"], booking_id, cash_fee, "CASH_FEE_REVERSAL")
            self.provider_modal.update_wallet(connection, booking["provider_id"], cash_fee)
        # Earlier cancellation fees in the total belong to another worker.
        total = int(booking.get("total_amount") or 0) - int(booking.get("dues_collected") or 0)
        fee = int(booking.get("platform_fee") or 0) or platform_fee_for(total)
=======
        # Coupons, coins and plan discounts are funded by the platform: the worker's
        # share is on the full job price (sub_total), not on what the customer paid.
        base = int(booking.get("sub_total") or 0) or int(booking.get("total_amount") or 0)
        fee = int(booking.get("platform_fee") or 0) or platform_fee_for(base)
>>>>>>> 2a6b265c84b73de9464fb5049c7afe06d757e943
        payment = self.modal.find_payment(connection, payment_id=booking.get("payment_id"))
        refunded = int((payment or {}).get("refund_amount") or 0)
        if refunded and base:
            # Part of the bill was refunded before completion: share only what was kept.
            kept = max(0, base - refunded)
            fee = int(fee * kept / base)
            base = kept
        earning = max(0, base - fee)
        if earning:
            self.modal.add_earning(connection, booking["provider_id"], booking_id, earning)
            self.provider_modal.update_wallet(connection, booking["provider_id"], earning)
        return earning

    def reverse_provider_earning(self, connection, booking, limit=None) -> int:
        """Booking refunded/cancelled after the worker was credited: take it back
        (at most `limit` paise when given)."""
        if not booking.get("provider_id"):
            return 0
        net = self.modal.net_earning(connection, booking["booking_id"], booking["provider_id"])
        if limit is not None:
            net = min(net, int(limit))
        if net <= 0:
            return 0
        # Same convention as other deductions: positive amount, type DEDUCTION.
        self.modal.add_earning(connection, booking["provider_id"], booking["booking_id"], net, "DEDUCTION")
        self.provider_modal.update_wallet(connection, booking["provider_id"], net, "debit")
        return net

    # ── refunds ──────────────────────────────────────────────────────────────

    def refund(self, connection, payment, amount=None, reason=None, raise_on_error=True) -> int:
        """Refund `amount` paise (default: everything not yet refunded). On a
        Razorpay failure the payment is flagged REFUND_FAILED for admin."""
        refundable = int(payment["amount"]) - int(payment.get("refund_amount") or 0)
        amount = refundable if amount is None else int(amount)
        if refundable <= 0:
            raise ValueError("This payment is already fully refunded")
        if amount <= 0 or amount > refundable:
            raise ValueError(f"You can refund at most {refundable} paise on this payment")
        if not payment.get("razorpay_payment_id"):
            raise ValueError("Payment has no Razorpay payment id")
        if not self.modal.reserve_refund(connection, payment, amount):
            raise ValueError("Payment changed meanwhile — reload and try again")
        try:
            rf = _get_razorpay_client().payment.refund(payment["razorpay_payment_id"], {
                "amount": amount,
                "notes": {"reason": (reason or "Refund")[:200]},
            })
            if rf and rf.get("id"):
                self.modal.update_payment(connection, payment["payment_id"], {"refund_id": rf["id"]})
        except Exception as e:
            print(f"[Refund] Razorpay refund failed for {payment['payment_id']}: {e}")
            if raise_on_error:
                raise ValueError(f"Razorpay refused the refund: {e}")  # request rolls back
            self.modal.undo_refund_reservation(connection, payment, amount)
            self._mirror_on_booking(connection, payment)
            return 0
        fresh = self._mirror_on_booking(connection, payment)
        if payment.get("purpose") == "SUBSCRIPTION" and fresh["status"] == "REFUNDED":
            # Money back → plan off.
            from subscriptions.subscriptions_modal import SubscriptionsMaster
            subs = SubscriptionsMaster()
            connection.execute(subs.subs.update()
                               .where(subs.subs.c.payment_id == payment["payment_id"])
                               .where(subs.subs.c.status == "ACTIVE")
                               .values(status="CANCELLED"))
        return amount

    def _mirror_on_booking(self, connection, payment):
        """Copy the payment's status onto its booking (only if it is the booking's payment)."""
        fresh = self.modal.find_payment(connection, payment_id=payment["payment_id"])
        if fresh.get("booking_id"):
            booking = self.booking_modal.read_one(connection, fresh["booking_id"])
            if str(booking.get("payment_id")) == str(fresh["payment_id"]):
                self.booking_modal.update_payment(connection, booking["booking_id"],
                                                  fresh["payment_id"], fresh["status"])
        return fresh

    def refund_booking_on_cancel(self, connection, booking, keep: int = 0) -> int:
        """Called by every cancel path. Never raises — a failed refund is flagged.
        `keep` (paise) is held back from the refund — a late-cancel fee. Returns
        how much was kept."""
        kept = 0
        if booking.get("payment_id") and booking.get("payment_status") in ("PAID", "PARTIALLY_REFUNDED"):
            payment = self.modal.find_payment(connection, payment_id=booking["payment_id"])
            if payment and payment.get("razorpay_payment_id"):
                refundable = int(payment["amount"]) - int(payment.get("refund_amount") or 0)
                kept = max(0, min(int(keep or 0), refundable))
                try:
                    if refundable - kept > 0:
                        self.refund(connection, payment, refundable - kept, "Booking cancelled", raise_on_error=False)
                except ValueError as e:
                    print(f"[Cancel] Refund skipped for {booking['booking_id']}: {e}")
        self.reverse_provider_earning(connection, booking)
        return kept

    def request_refund(self, obj, connection):
        """Admin/support: POST /payments/refund {booking_id | payment_id, amount?(paise), reason?}."""
        obj.pop("_user_id", None)
        role = obj.pop("_role", None)
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Admin or Support role required")
        data = RefundSchema(**obj)
        if data.payment_id:
            payment = self.modal.find_payment(connection, payment_id=data.payment_id)
        else:
            booking = self.booking_modal.read_one(connection, data.booking_id)
            payment = self.modal.find_payment(connection, payment_id=booking.get("payment_id"))
        if not payment or payment["status"] not in ("PAID", "PARTIALLY_REFUNDED", "REFUND_FAILED"):
            raise ValueError("No paid payment found")
        refunded = self.refund(connection, payment, data.amount, data.reason or "Refund by support")
        clawed = 0
        if data.deduct_from_worker and payment.get("booking_id"):
            # Admin's choice: the worker bears this refund (up to what they were credited).
            booking = self.booking_modal.read_one(connection, payment["booking_id"])
            clawed = self.reverse_provider_earning(connection, booking, limit=refunded)
        fresh = self.modal.find_payment(connection, payment_id=payment["payment_id"])
        return "success", {"message": "Refund initiated", "refunded": refunded, "deducted_from_worker": clawed,
                           "refund_amount": fresh["refund_amount"], "status": fresh["status"]}

    # ── Razorpay webhook ─────────────────────────────────────────────────────

    def webhook(self, obj, connection):
        """POST /payments/webhook (public; HMAC of the raw body). Covers the app
        being closed between paying and calling /verify, failures and refunds."""
        raw = obj.get("_raw_body") or ""
        signature = obj.get("_rzp_signature") or ""
        expected = hmac.new(_secret("RAZORPAY_WEBHOOK_SECRET").encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise PermissionError("Invalid webhook signature")
        event = json.loads(raw)
        kind = event.get("event", "")
        payload = event.get("payload") or {}
        entity = (payload.get("payment") or {}).get("entity") or {}

        if kind in ("payment.authorized", "payment.captured", "order.paid"):
            payment = self.modal.find_payment(connection, razorpay_order_id=entity.get("order_id"))
            if not payment:
                return "success", {"ignored": "unknown order"}
            if self._is_extra_payment(payment, entity.get("id")):
                self._release_extra_payment(payment, entity["id"])
                return "success", {"outcome": "extra_payment_released"}
            if int(entity.get("amount") or 0) != int(payment["amount"]):
                print(f"[Webhook] amount mismatch on {payment['payment_id']}")
                return "success", {"ignored": "amount mismatch"}
            if entity.get("status") == "authorized":
                try:
                    _get_razorpay_client().payment.capture(entity["id"], int(payment["amount"]), {"currency": CURRENCY})
                except Exception as e:
                    print(f"[Webhook] capture failed for {entity.get('id')}: {e}")
            outcome = self.confirm_payment(connection, payment, entity["id"], entity.get("method"))
            return "success", {"outcome": outcome}

        if kind == "payment.failed":
            payment = self.modal.find_payment(connection, razorpay_order_id=entity.get("order_id"))
            if payment:
                self.modal.mark_failed(connection, payment["payment_id"])
            return "success", {"outcome": "failed"}

        if kind == "refund.failed":
            refund = (payload.get("refund") or {}).get("entity") or {}
            payment = self.modal.find_payment(connection, razorpay_payment_id=refund.get("payment_id"))
            if payment and self.modal.undo_refund_by_id(connection, payment, refund.get("id"),
                                                         int(refund.get("amount") or 0)):
                self._mirror_on_booking(connection, payment)
                return "success", {"outcome": "refund_failed"}
            return "success", {"ignored": "refund already handled"}

        return "success", {"ignored": kind}

    # ── worker dues (fees on cash jobs) ──────────────────────────────────────

    def dues_order(self, obj, connection):
        """POST /providers/me/dues/order — a Razorpay order for everything the worker owes.
        The app then calls POST /providers/me/dues/verify (same body as /payments/verify)."""
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        provider = self.provider_modal.find_by_user_id(connection, user_id)
        if not provider:
            raise ValueError("Provider profile not found")
        owed = max(0, -int(provider.get("wallet_balance") or 0))
        if owed <= 0:
            raise ValueError("You don't owe anything")
        payment = self.modal.find_open_order(connection, user_id, owed, purpose="WORKER_DUES")
        if not payment:
            rz_order = _get_razorpay_client().order.create({
                "amount": owed,
                "currency": CURRENCY,
                "receipt": f"dues-{provider['provider_id']}"[:40],
                "notes": {"purpose": "WORKER_DUES", "provider_id": provider["provider_id"]},
            })
            payment = self.modal.create_payment(connection, {
                "purpose": "WORKER_DUES",
                "customer_id": user_id,
                "razorpay_order_id": rz_order["id"],
                "amount": owed,
                "currency": CURRENCY,
                "status": "PENDING",
            })
        return "success", {
            "razorpay_order_id": payment["razorpay_order_id"],
            "amount": owed,
            "currency": CURRENCY,
            "payment_id": payment["payment_id"],
        }

    def dues_verify(self, obj, connection):
        """POST /providers/me/dues/verify — the checkout success callback for dues."""
        if obj.get("_role") != "PROVIDER":
            raise PermissionError("Provider role required")
        payment = self.modal.find_payment(connection, razorpay_order_id=obj.get("razorpay_order_id"))
        if not payment or payment.get("purpose") != "WORKER_DUES":
            raise ValueError("Payment record not found")
        return self.verify_payment(obj, connection)

    # ── payouts / admin list ─────────────────────────────────────────────────

    def payout_request(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        data = PayoutRequestSchema(**obj)
        provider = self.provider_modal.find_by_user_id(connection, user_id)
        if not provider:
            raise ValueError("Provider profile not found")
        # Lock the provider row so concurrent requests are serialised, then
        # count money already reserved by open payout requests.
        available = self.provider_modal.lock_available_balance(connection, provider["provider_id"])
        if available < data.amount:
            pending = int(provider["wallet_balance"] or 0) - available
            if pending > 0:
                raise ValueError(
                    f"Insufficient balance: ₹{pending / 100:.2f} is already held by pending payout requests "
                    f"(available to withdraw: ₹{max(0, available) / 100:.2f})"
                )
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

    # ── helpers ──────────────────────────────────────────────────────────────

    def _provider_user_id(self, connection, booking):
        if not booking.get("provider_id"):
            return None
        prov = self.provider_modal.find_by_id(connection, booking["provider_id"])
        return str(prov["user_id"]) if prov else None

    def _push(self, connection, user_ids, title, body, data):
        user_ids = [str(u) for u in user_ids if u]
        if not user_ids:
            return
        try:
            self.notif.send_push(connection=connection, user_ids=user_ids, title=title, body=body, data=data)
        except Exception as e:
            print(f"[Payment] Push notification failed (non-fatal): {e}")


def _verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    secret = _secret("RAZORPAY_KEY_SECRET")
    msg = f"{order_id}|{payment_id}"
    expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")
