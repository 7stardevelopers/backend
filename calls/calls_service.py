import os
import requests

from calls.calls_modal import CallsMaster
from calls.calls_validator import InitiateCallSchema, CallStatusCallbackSchema
from providers.providers_modal import ProvidersMaster
from utilities.db_connection import get_table


class CallsService:
    def __init__(self):
        self.modal = CallsMaster()

    def initiate_call(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        data = InitiateCallSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})

        users_t = get_table("users")
        caller_number = callee_number = None

        if data.booking_id:
            if not data.target:
                raise ValueError("target ('customer' or 'provider') is required for a booking call")

            bookings_t = get_table("bookings")
            booking = connection.execute(
                bookings_t.select().where(bookings_t.c.booking_id == data.booking_id)
            ).mappings().fetchone()
            if not booking:
                raise ValueError("Booking not found")
            if not booking["provider_id"]:
                raise ValueError("No provider assigned to this booking yet")

            provider = ProvidersMaster().find_by_id(connection, booking["provider_id"])
            if not provider:
                raise ValueError("Provider not found")

            if role == "CUSTOMER":
                if str(booking["customer_id"]) != str(user_id):
                    raise PermissionError("Not your booking")
                if data.target != "provider":
                    raise PermissionError("Customers may only call the assigned provider")
            elif role == "PROVIDER":
                if str(provider["user_id"]) != str(user_id):
                    raise PermissionError("Not your booking")
                if data.target != "customer":
                    raise PermissionError("Providers may only call the customer")
            elif role not in ("ADMIN", "SUPPORT"):
                raise PermissionError("Not authorized to place calls")

            customer_row = connection.execute(
                users_t.select().where(users_t.c.user_id == booking["customer_id"])
            ).mappings().fetchone()
            provider_user_row = connection.execute(
                users_t.select().where(users_t.c.user_id == provider["user_id"])
            ).mappings().fetchone()

            if data.target == "provider":
                caller_number, callee_number = customer_row["phone"], provider_user_row["phone"]
            else:
                caller_number, callee_number = provider_user_row["phone"], customer_row["phone"]

        elif data.target_user_id:
            if role not in ("ADMIN", "SUPPORT"):
                raise PermissionError("Only admin/support may call a user directly")
            target_row = connection.execute(
                users_t.select().where(users_t.c.user_id == data.target_user_id)
            ).mappings().fetchone()
            if not target_row:
                raise ValueError("Target user not found")
            admin_row = connection.execute(
                users_t.select().where(users_t.c.user_id == user_id)
            ).mappings().fetchone()
            caller_number, callee_number = admin_row["phone"], target_row["phone"]
        else:
            raise ValueError("booking_id or target_user_id is required")

        sid = self._connect_via_exotel(caller_number, callee_number)

        call_row = self.modal.create(connection, {
            "booking_id": data.booking_id,
            "initiated_by": user_id,
            "target": (data.target.upper() if data.target else "DIRECT"),
            "exotel_call_sid": sid,
            "status": "INITIATED",
        })
        return "success", {"message": "Call initiated", "call_id": call_row["call_id"]}

    def _connect_via_exotel(self, from_number: str, to_number: str) -> str:
        sid = os.environ.get("EXOTEL_SID")
        api_key = os.environ.get("EXOTEL_API_KEY")
        api_token = os.environ.get("EXOTEL_API_TOKEN")
        subdomain = os.environ.get("EXOTEL_SUBDOMAIN")
        exophone = os.environ.get("EXOPHONE")
        callback_url = os.environ.get("EXOTEL_STATUS_CALLBACK_URL", "")

        url = f"https://{api_key}:{api_token}@{subdomain}/v1/Accounts/{sid}/Calls/connect.json"
        payload = {
            "From": from_number,
            "To": to_number,
            "CallerId": exophone,
        }
        if callback_url:
            # Ask Exotel to POST the status callback as JSON — this Lambda's
            # request parser (request_handler.parse_request) only understands
            # a JSON body and silently drops anything else, so the default
            # form-urlencoded callback would arrive empty.
            payload["StatusCallback"] = callback_url
            payload["StatusCallbackContentType"] = "application/json"
        resp = requests.post(url, data=payload, timeout=10)
        resp.raise_for_status()
        return resp.json().get("Call", {}).get("Sid", "")

    def status_callback(self, obj, connection):
        data = CallStatusCallbackSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        status = data.DialCallStatus or data.Status or "UNKNOWN"
        self.modal.update_status_by_sid(connection, data.CallSid, status)
        return "success", {"message": "OK"}
