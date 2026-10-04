from support.support_modal import SupportMaster
from support.support_validator import CreateTicketSchema, UpdateTicketSchema, ReplySchema
from notifications.notifications_service import NotificationsService


class SupportService:
    def __init__(self):
        self.modal = SupportMaster()
        self.notif = NotificationsService()

    def create_ticket(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        data = CreateTicketSchema(**obj)
        if data.booking_id and role not in ("ADMIN", "SUPPORT"):
            _require_booking_participant(connection, data.booking_id, user_id)
        ticket_data = {
            "user_id": user_id,
            "subject": data.subject,
            "category": data.category,
            "booking_id": data.booking_id,
            "priority": data.priority,
        }
        is_sos = data.category == "SAFETY" and data.booking_id and role == "CUSTOMER"
        if is_sos:
            ticket_data["priority"] = "URGENT"
        ticket = self.modal.create_ticket(connection, ticket_data)
        if is_sos:
            self._escalate_sos(connection, ticket, data.booking_id)
        return "created", ticket

    def _escalate_sos(self, connection, ticket, booking_id):
        """SOS from the tracking screen: pin the expert's last known position to
        the ticket (internal note) and alert every admin/support user. Non-fatal."""
        try:
            from sqlalchemy import text
            row = connection.execute(text("""
                SELECT b.status, u.name AS provider_name, u.phone AS provider_phone,
                       pl.lat, pl.lng, pl.updated_at
                FROM bookings b
                LEFT JOIN providers p ON p.provider_id = b.provider_id
                LEFT JOIN users u ON u.user_id = p.user_id
                LEFT JOIN provider_locations pl ON pl.provider_id = b.provider_id
                WHERE b.booking_id = :bid
            """), {"bid": booking_id}).fetchone()
            if row:
                where = (f"https://maps.google.com/?q={row.lat},{row.lng} (at {row.updated_at} UTC)"
                         if row.lat is not None else "unknown")
                note = (f"SOS raised during booking ({row.status}). Expert: {row.provider_name or '-'} "
                        f"{row.provider_phone or ''}. Last expert location: {where}")
                self.modal.add_message(connection, ticket["ticket_id"], ticket["user_id"], note, True)
            staff = connection.execute(text(
                "SELECT user_id FROM users WHERE role IN ('ADMIN', 'SUPPORT')"
            )).fetchall()
            if staff:
                self.notif.send_push(
                    connection=connection,
                    user_ids=[str(r.user_id) for r in staff],
                    title="🚨 SOS from a customer",
                    body=ticket["subject"][:100],
                    data={"type": "sos", "ticket_id": ticket["ticket_id"], "booking_id": str(booking_id)},
                )
        except Exception as e:
            print(f"[Support] SOS escalation failed (non-fatal): {e}")

    def list_mine(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        page = int(obj.get("page", 1))
        tickets = self.modal.list_by_user(connection, user_id, page)
        return "success", tickets

    def admin_list(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Admin or Support role required")
        page = int(obj.get("page", 1))
        status = obj.get("status")
        priority = obj.get("priority")
        tickets = self.modal.list_all(connection, status=status, priority=priority, page=page)
        return "success", tickets

    def get_detail(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        ticket_id = obj.get("id") or obj.get("ticket_id")
        ticket = self.modal.get_one(connection, ticket_id)
        if role not in ("ADMIN", "SUPPORT") and str(ticket["user_id"]) != str(user_id):
            raise PermissionError("Access denied")
        staff = role in ("ADMIN", "SUPPORT")
        ticket["messages"] = self.modal.get_messages(connection, ticket_id, include_internal=staff)
        return "success", ticket

    def update_status(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Admin or Support role required")
        ticket_id = obj.get("id") or obj.get("ticket_id")
        data = UpdateTicketSchema(**{k: v for k, v in obj.items() if k not in ("id",)})
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        self.modal.update(connection, ticket_id, fields)
        if fields.get("status") == "RESOLVED":
            fields["resolved_at"] = __import__("utilities.common_table_elements", fromlist=["now_utc"]).now_utc()
            self.modal.update(connection, ticket_id, {"resolved_at": fields["resolved_at"]})
        return "success", {"message": "Ticket updated"}

    def reply(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        ticket_id = obj.get("id") or obj.get("ticket_id")
        ticket = self.modal.get_one(connection, ticket_id)
        if role not in ("ADMIN", "SUPPORT") and str(ticket["user_id"]) != str(user_id):
            raise PermissionError("Access denied")
        data = ReplySchema(**{k: v for k, v in obj.items() if k not in ("id",)})
        is_internal = data.is_internal and role in ("ADMIN", "SUPPORT")
        message = self.modal.add_message(
            connection, ticket_id, user_id, data.content, is_internal
        )
        if role in ("ADMIN", "SUPPORT") and not is_internal:
            try:
                self.notif.send_push(
                    connection=connection,
                    user_ids=[ticket["user_id"]],
                    title="Support Reply",
                    body=data.content[:100],
                    data={"type": "support_reply", "ticket_id": ticket_id},
                )
            except Exception:
                pass
        return "created", message


def _require_booking_participant(connection, booking_id, user_id):
    from sqlalchemy import text
    row = connection.execute(text("""
        SELECT 1 FROM bookings b
        LEFT JOIN providers p ON p.provider_id = b.provider_id
        WHERE b.booking_id = :bid AND (b.customer_id = :uid OR p.user_id = :uid)
    """), {"bid": booking_id, "uid": user_id}).fetchone()
    if not row:
        raise PermissionError("You can only raise tickets for your own bookings")
