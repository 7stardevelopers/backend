from bookings.bookings_modal import BookingsMaster
from bookings.bookings_service import BookingsService, door_otp_expired
from identity_reports.identity_reports_modal import IdentityReportsMaster
from identity_reports.identity_reports_validator import DoorCheckSchema, ResolveReportSchema
from providers.providers_modal import ProvidersMaster
from utilities.common_table_elements import now_utc

DOOR_CHECK_STATUSES = ("ACCEPTED", "EN_ROUTE")


class IdentityReportsService:
    """At the door, the customer compares the worker with their profile selfie
    before the door OTP exists: POST /bookings/{id}/identity-check.
      match=true  → door OTP is generated (or the live one returned) for the customer to read out.
      match=false → no OTP; an identity report + urgent support ticket go to admin."""

    def __init__(self):
        self.modal = IdentityReportsMaster()
        self.bookings = BookingsMaster()
        self.booking_svc = BookingsService()

    def door_check(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        booking_id = obj.get("id") or obj.get("booking_id")
        data = DoorCheckSchema(**{k: v for k, v in obj.items() if k in ("match", "note")})
        booking = self.bookings.read_one(connection, booking_id)
        if role != "CUSTOMER" or str(booking.get("customer_id")) != str(user_id):
            raise PermissionError("Access denied")
        if not booking.get("provider_id"):
            raise ValueError("No expert has been assigned yet")
        if booking["status"] not in DOOR_CHECK_STATUSES or booking.get("door_otp_verified"):
            raise ValueError("The expert has already been checked in for this booking")
        if data.match:
            return self._confirmed(connection, booking)
        return self._mismatch(connection, booking, user_id, role, (data.note or "").strip() or None)

    def _confirmed(self, connection, booking):
        booking_id = booking["booking_id"]
        live = booking.get("identity_confirmed_at") and booking.get("door_otp") and not door_otp_expired(booking)
        if not live:
            self.bookings.regenerate_door_otp(connection, booking_id)
            self.bookings.confirm_identity(connection, booking_id)
            self.booking_svc._push(
                connection, [self.booking_svc._provider_user_id(connection, booking)],
                "Customer confirmed it's you",
                "Ask the customer for the 4-digit door code to start the job.",
                {"type": "identity_confirmed", "booking_id": booking_id},
            )
        fresh = self.bookings.read_one(connection, booking_id)
        return "success", {
            "match": True,
            "door_otp": fresh["door_otp"],
            "identity_confirmed_at": fresh["identity_confirmed_at"],
        }

    def _mismatch(self, connection, booking, user_id, role, note):
        booking_id = booking["booking_id"]
        self.bookings.mark_identity_mismatch(connection, booking_id)
        report = self.modal.find_by_booking(connection, booking_id)
        if not report:
            provider = ProvidersMaster().find_by_id(connection, booking["provider_id"]) or {}
            from support.support_service import SupportService
            support = SupportService()
            # SAFETY + booking from a customer → URGENT, worker's last location pinned,
            # every admin/support user pushed (SupportService._escalate_sos).
            _, ticket = support.create_ticket({
                "_user_id": user_id, "_role": role,
                "subject": f"Wrong person at the door — not {provider.get('name') or 'the assigned expert'}"[:200],
                "category": "SAFETY", "booking_id": booking_id, "priority": "URGENT",
            }, connection)
            support.modal.add_message(
                connection, ticket["ticket_id"], user_id,
                note or "The person at my door doesn't match the expert's profile photo.",
            )
            report = self.modal.create(connection, booking, note=note, ticket_id=ticket["ticket_id"])
        return "success", {
            "match": False,
            "report_id": report["report_id"],
            "ticket_id": report.get("ticket_id"),
            "message": "Thanks for telling us. Please don't let them in — our team will call you shortly.",
        }

    # ── admin ────────────────────────────────────────────────────────────────

    def admin_list(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Admin or Support role required")
        status = obj.get("status") or None
        page = int(obj.get("page", 1))
        per_page = int(obj.get("per_page", 20))
        return "success", self.modal.list_detailed(connection, status, page, per_page)

    def admin_resolve(self, obj, connection):
        role = obj.pop("_role", None)
        user_id = obj.pop("_user_id", None)
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Admin or Support role required")
        report_id = obj.get("id")
        data = ResolveReportSchema(**{k: v for k, v in obj.items() if k in ("status", "admin_note")})
        report = self.modal.find_by_id(connection, report_id)
        if not report:
            raise ValueError("Report not found")
        open_again = data.status == "OPEN"
        fields = {
            "status": data.status,
            "resolved_by": None if open_again else user_id,
            "resolved_at": None if open_again else now_utc(),
        }
        if data.admin_note is not None:
            fields["admin_note"] = data.admin_note
        self.modal.update(connection, report_id, fields)
        try:
            from admin.admin_modal import AdminMaster
            AdminMaster().write_log(connection, user_id, f"IDENTITY_REPORT_{data.status}",
                                    "identity_report", report_id)
        except Exception:
            pass
        return "success", self.modal.find_by_id(connection, report_id)
