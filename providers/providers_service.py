from providers.providers_modal import ProvidersMaster
from providers.providers_validator import (
    UpdateProviderProfileSchema, UpdateLocationSchema,
    ToggleAvailabilitySchema, NearbyProvidersSchema, SetServicesSchema, SetDocumentsSchema,
)
from documents.documents_modal import DocumentsMaster
from notifications.notifications_service import NotificationsService
from utilities.common_table_elements import new_uuid


class ProvidersService:
    def __init__(self):
        self.modal = ProvidersMaster()
        self.notif = NotificationsService()
        self.docs_modal = DocumentsMaster()

    def _get_or_create_provider(self, conn, user_id: str):
        provider = self.modal.find_by_user_id(conn, user_id)
        if not provider:
            provider = self.modal.create(conn, user_id)
        return provider

    def get_my_profile(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        provider = self._get_or_create_provider(connection, user_id)
        provider["services"] = self.modal.get_services(connection, provider["provider_id"])
        return "success", provider

    def update_profile(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        provider = self._get_or_create_provider(connection, user_id)
        data = UpdateProviderProfileSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        if fields:
            self.modal.update(connection, provider["provider_id"], fields)
        return "success", {"message": "Profile updated"}

    def set_documents(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        provider = self._get_or_create_provider(connection, user_id)
        data = SetDocumentsSchema(**obj)
        key_to_type = {
            "aadhaar_front": "AADHAAR_FRONT",
            "aadhaar_back":  "AADHAAR_BACK",
            "pan":           "PAN",
        }
        for field, doc_type in key_to_type.items():
            url = getattr(data, field)
            if url:
                self.docs_modal.upsert_by_type(connection, provider["provider_id"], doc_type, url)
        return "success", {"message": "Documents saved"}

    def set_services(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        provider = self._get_or_create_provider(connection, user_id)
        data = SetServicesSchema(**obj)
        self.modal.set_services(connection, provider["provider_id"], data.service_ids)
        return "success", {"message": "Services updated"}

    def toggle_availability(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        data = ToggleAvailabilitySchema(**obj)
        provider = self._get_or_create_provider(connection, user_id)
        self.modal.update(connection, provider["provider_id"], {"is_available": data.is_available})
        return "success", {"is_available": data.is_available}

    def update_location(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        data = UpdateLocationSchema(**obj)
        provider = self._get_or_create_provider(connection, user_id)
        self.modal.upsert_location(connection, provider["provider_id"], data.lat, data.lng)
        self.modal.update(connection, provider["provider_id"], {"last_lat": data.lat, "last_lng": data.lng, "last_seen_at": __import__("utilities.common_table_elements", fromlist=["now_utc"]).now_utc()})
        # Broadcast live location to customer for any active booking this provider is on
        try:
            from web_sockets.web_sockets_service import _broadcast_location_to_customer
            from utilities.db_connection import get_table
            bookings_t = get_table("bookings")
            active = connection.execute(
                bookings_t.select()
                .where(bookings_t.c.provider_id == provider["provider_id"])
                .where(bookings_t.c.status.in_(["EN_ROUTE", "IN_PROGRESS"]))
            ).fetchone()
            if active:
                _broadcast_location_to_customer(connection, active["booking_id"], data.lat, data.lng)
        except Exception as e:
            print(f"[Location] WS broadcast failed (non-fatal): {e}")
        return "success", {"message": "Location updated"}

    def get_nearby(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role not in ("ADMIN", "CUSTOMER"):
            raise PermissionError("Admin or customer role required")
        data = NearbyProvidersSchema(**obj)
        from providers.provider_matching import haversine
        all_providers = self.modal.list_all(connection, status="APPROVED")
        nearby = []
        for p in all_providers:
            lat = p.get("last_lat")
            lng = p.get("last_lng")
            if lat and lng:
                dist = haversine(data.lat, data.lng, float(lat), float(lng))
                if dist <= data.radius_km:
                    p["distance_km"] = round(dist, 2)
                    nearby.append(p)
        nearby.sort(key=lambda x: x["distance_km"])
        return "success", nearby

    def get_public_profile(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        provider_id = obj.get("id") or obj.get("provider_id")
        provider = self.modal.find_by_id(connection, provider_id)
        if not provider:
            raise ValueError("Provider not found")
        safe = {k: v for k, v in provider.items() if k not in ("bank_account_number", "bank_ifsc", "wallet_balance")}
        return "success", safe

    def admin_list(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        page = int(obj.get("page", 1))
        status = obj.get("status")
        providers = self.modal.list_all(connection, status=status, page=page)
        return "success", providers

    def admin_approve(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        provider_id = obj.get("id") or obj.get("provider_id")
        self.modal.update(connection, provider_id, {"status": "APPROVED"})
        provider = self.modal.find_by_id(connection, provider_id)
        try:
            self.notif.send_push(
                connection=connection,
                user_ids=[provider["user_id"]],
                title="Application Approved!",
                body="Congratulations! You can now go online and start accepting jobs.",
                data={"type": "provider_approved"},
            )
        except Exception:
            pass
        return "success", {"message": "Provider approved"}

    def admin_suspend(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        provider_id = obj.get("id") or obj.get("provider_id")
        reason = obj.get("reason", "")
        self.modal.update(connection, provider_id, {"status": "SUSPENDED", "is_available": False})
        return "success", {"message": "Provider suspended"}

    def get_my_earnings(self, obj, connection):
        user_id = obj.pop("_user_id")
        role    = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        provider = self._get_or_create_provider(connection, user_id)
        return "success", self.modal.get_earnings(connection, provider["provider_id"])

    def admin_list_detailed(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        status   = obj.get("status")
        page     = int(obj.get("page", 1))
        per_page = int(obj.get("per_page", 20))
        return "success", self.modal.list_all_detailed(connection, status, page, per_page)
