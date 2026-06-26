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
        ticket_data = {
            "user_id": user_id,
            "subject": data.subject,
            "category": data.category,
            "booking_id": data.booking_id,
            "priority": data.priority,
        }
        ticket = self.modal.create_ticket(connection, ticket_data)
        return "created", ticket

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
        ticket["messages"] = self.modal.get_messages(connection, ticket_id)
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
        message = self.modal.add_message(
            connection, ticket_id, user_id, data.content, data.is_internal
        )
        if role in ("ADMIN", "SUPPORT") and not data.is_internal:
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
