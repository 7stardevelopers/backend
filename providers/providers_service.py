from providers.providers_modal import ProvidersMaster
from providers.providers_validator import (
    UpdateProviderProfileSchema, UpdateLocationSchema,
    ToggleAvailabilitySchema, NearbyProvidersSchema, SetServicesSchema, SetDocumentsSchema,
)
from documents.documents_modal import DocumentsMaster
from notifications.notifications_service import NotificationsService
from utilities.common_table_elements import new_uuid


# TEMPORARY — see temp_self_approve
TEMP_SELF_APPROVE_PHONES = {"9390233299"}


class ProvidersService:
    def __init__(self):
        self.modal = ProvidersMaster()
        self.notif = NotificationsService()
        self.docs_modal = DocumentsMaster()

    def _get_or_create_provider(self, conn, user_id: str, role=None):
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        provider = self.modal.find_by_user_id(conn, user_id)
        if not provider:
            provider = self.modal.create(conn, user_id)
        return provider

    def get_my_profile(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        provider = self._get_or_create_provider(connection, user_id, role)
        provider["services"] = self.modal.get_services(connection, provider["provider_id"])
        return "success", provider

    def update_profile(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        provider = self._get_or_create_provider(connection, user_id, role)
        data = UpdateProviderProfileSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        if fields:
            self.modal.update(connection, provider["provider_id"], fields)
        return "success", {"message": "Profile updated"}

    def set_documents(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        provider = self._get_or_create_provider(connection, user_id, role)
        data = SetDocumentsSchema(**obj)
        key_to_type = {
            "aadhaar_front": "AADHAAR_FRONT",
            "aadhaar_back":  "AADHAAR_BACK",
            "pan":           "PAN",
        }
        from media.media_service import is_own_upload
        for field, doc_type in key_to_type.items():
            url = getattr(data, field)
            if url and not is_own_upload(url, user_id, "documents"):
                raise ValueError(f"{field} must be a file you uploaded via /media/presign")
        for field, doc_type in key_to_type.items():
            url = getattr(data, field)
            if url:
                self.docs_modal.upsert_by_type(connection, provider["provider_id"], doc_type, url)
        return "success", {"message": "Documents saved"}

    def set_services(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        provider = self._get_or_create_provider(connection, user_id, role)
        data = SetServicesSchema(**obj)
        self.modal.set_services(connection, provider["provider_id"], data.service_ids)
        return "success", {"message": "Services updated"}

    def toggle_availability(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        data = ToggleAvailabilitySchema(**obj)
        provider = self._get_or_create_provider(connection, user_id, role)
        self.modal.update(connection, provider["provider_id"], {"is_available": data.is_available})
        return "success", {"is_available": data.is_available}

    # TEMPORARY (testing only) — remove once the test worker is approved.
    # Lets one hard-coded test number approve its own provider profile, because
    # there is no admin account / DB access on staging yet. Never in production.
    def temp_self_approve(self, obj, connection):
        from utilities.auth_tokens import is_production
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if is_production():
            raise PermissionError("Not available")
        provider = self._get_or_create_provider(connection, user_id, role)
        from auth.authorization_modal import UsersMaster
        user = UsersMaster().find_by_id(connection, user_id)
        if not user or user.get("phone") not in TEMP_SELF_APPROVE_PHONES:
            raise PermissionError("Not available")
        self.modal.update(connection, provider["provider_id"], {"status": "APPROVED"})
        print(f"[TEMP] Self-approved test provider {provider['provider_id']}")
        return "success", {"message": "Approved (testing)", "status": "APPROVED"}

    def report_location_revoked(self, obj, connection):
        # Authoritative server-side flip to offline — called by the worker app
        # the moment it detects location permission was revoked, rather than
        # relying solely on the client to remember to toggle itself off.
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        provider = self._get_or_create_provider(connection, user_id, role)
        self.modal.update(connection, provider["provider_id"], {"is_available": False})
        return "success", {"is_available": False}

    def update_location(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        data = UpdateLocationSchema(**obj)
        provider = self._get_or_create_provider(connection, user_id, role)
        if data.mocked:
            # Fake-GPS apps would let a worker "arrive" from home. Allowed outside
            # production so the route can be simulated while testing.
            from utilities.auth_tokens import is_production
            print(f"[Location] mocked fix from provider {provider['provider_id']}")
            if is_production():
                raise ValueError("Mock locations are not allowed")
        self.modal.upsert_location(connection, provider["provider_id"], data.lat, data.lng)
        from utilities.common_table_elements import now_utc
        from bookings.live_tracking import _as_utc
        now = now_utc()
        # provider_locations above is the live source; the providers row (used for
        # matching) only needs refreshing every PROVIDER_ROW_REFRESH_S.
        last_seen = _as_utc(provider.get("last_seen_at"))
        if last_seen is None or (now - last_seen).total_seconds() >= PROVIDER_ROW_REFRESH_S:
            self.modal.update(connection, provider["provider_id"], {"last_lat": data.lat, "last_lng": data.lng, "last_seen_at": now})
        # Broadcast live location only to customers of live-trackable bookings — a
        # booking ACCEPTED for later must not see where the provider is right now.
        try:
            from web_sockets.web_sockets_service import _broadcast_location_to_customer
            from bookings.live_tracking import live_tracking_bookings, booking_destination, check_arrival, check_late
            for b in live_tracking_bookings(connection, provider["provider_id"]):
                extra = {"heading": data.heading, "speed": data.speed, "accuracy": data.accuracy}
                eta = None
                dest = booking_destination(b) if b.status == "EN_ROUTE" else None
                if dest:
                    eta = _cached_road_eta(b.booking_id, (data.lat, data.lng), dest)
                    extra["eta"] = eta
                _broadcast_location_to_customer(connection, b.booking_id, data.lat, data.lng, updated_at=now, extra=extra)
                if not check_arrival(connection, b, data.lat, data.lng, eta):
                    check_late(connection, b, eta)
        except Exception as e:
            print(f"[Location] WS broadcast failed (non-fatal): {e}")
        return "success", {"message": "Location updated"}

    def admin_list_locations(self, obj, connection):
        obj.pop("_user_id", None)
        role = obj.pop("_role", None)
        if role not in ("ADMIN", "SUPPORT"):
            raise PermissionError("Admin role required")
        return "success", self.modal.list_all_locations(connection)

    def get_nearby(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role not in ("ADMIN", "CUSTOMER"):
            raise PermissionError("Admin or customer role required")
        data = NearbyProvidersSchema(**obj)
        from providers.provider_matching import haversine
        nearby = []
        for p in self.modal.list_nearby_candidates(connection, data.service_id):
            dist = haversine(data.lat, data.lng, float(p["last_lat"]), float(p["last_lng"]))
            if dist <= data.radius_km:
                p["distance_km"] = round(dist, 2)
                nearby.append(p)
        nearby.sort(key=lambda x: x["distance_km"])
        return "success", nearby

    def get_public_profile(self, obj, connection):
        user_id = obj.pop("_user_id", None)
        role = obj.pop("_role", None)
        provider_id = obj.get("id") or obj.get("provider_id")
        provider = self.modal.find_by_id(connection, provider_id)
        if not provider:
            raise ValueError("Provider not found")

        if role not in ("ADMIN", "SUPPORT"):
            from utilities.db_connection import get_table
            bookings_t = get_table("bookings")
            has_relationship = connection.execute(
                bookings_t.select()
                .where(bookings_t.c.customer_id == user_id)
                .where(bookings_t.c.provider_id == provider_id)
                .where(bookings_t.c.status.in_(["ACCEPTED", "EN_ROUTE", "IN_PROGRESS", "COMPLETED"]))
            ).fetchone()
            if not has_relationship:
                raise PermissionError("No booking relationship with this provider")

        safe = {k: v for k, v in provider.items() if k not in ("bank_account_number", "bank_ifsc", "wallet_balance")}
        return "success", safe

    def admin_update_bio(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        provider_id = obj.get("id") or obj.get("provider_id")
        bio = obj.get("bio")
        if bio is None:
            raise ValueError("bio is required")
        self.modal.update(connection, provider_id, {"bio": bio})
        return "success", {"message": "Bio updated"}

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
        provider = self._get_or_create_provider(connection, user_id, role)
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


PROVIDER_ROW_REFRESH_S = 30


def _cached_road_eta(booking_id, origin, dest):
    """Shared road ETA (cached 30 s in Redis), or None on any failure."""
    from bookings.booking_eta import road_eta
    from utilities.redis_connection import get_redis
    try:
        redis_client = get_redis()
    except Exception:
        redis_client = None
    try:
        return road_eta(booking_id, origin, dest, redis_client)
    except Exception as e:
        print(f"[Location] ETA failed (non-fatal): {e}")
        return None
