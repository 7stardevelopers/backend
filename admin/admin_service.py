from admin.admin_modal import AdminMaster
from admin.admin_validator import (
    UpdateBookingSchema, CreateServiceAdminSchema, CreateSubCategorySchema,
    CreateCategorySchema, UpdateCategorySchema,
    CreateSubscriptionPlanSchema, UpdateSubscriptionPlanSchema,
    SendAnnouncementSchema,
)


class AdminService:
    def __init__(self):
        self.modal = AdminMaster()

    def _require_admin(self, role):
        if role != "ADMIN":
            raise PermissionError("Admin role required")

    def dashboard_stats(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        stats = self.modal.get_dashboard_stats(connection)
        return "success", stats

    def list_all_bookings(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        page = int(obj.get("page", 1))
        status = obj.get("status")
        bookings = self.modal.list_all_bookings(connection, status=status, page=page)
        return "success", bookings

    def update_booking(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        booking_id = obj.pop("id", None) or obj.pop("booking_id", None)
        data = UpdateBookingSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        self.modal.update_booking(connection, booking_id, fields)
        return "success", {"message": "Booking updated"}

    def list_users(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        page = int(obj.get("page", 1))
        users = self.modal.list_users(connection, page=page)
        return "success", users

    def list_logs(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        page = int(obj.get("page", 1))
        logs = self.modal.list_logs(connection, page=page)
        return "success", logs

    def list_services(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        from services_catalog.services_modal import ServicesMaster
        category_id = obj.get("categoryId") or obj.get("category_id")
        services = ServicesMaster().list_services_admin(connection, category_id)
        return "success", services

    def list_categories(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        from services_catalog.services_modal import ServicesMaster
        categories = ServicesMaster().list_categories_admin(connection)
        return "success", categories

    def create_category(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        from services_catalog.services_modal import ServicesMaster
        data = CreateCategorySchema(**obj)
        result = ServicesMaster().create_category(connection, data.model_dump())
        return "created", result

    def create_service(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        from services_catalog.services_modal import ServicesMaster
        data = CreateServiceAdminSchema(**obj)
        result = ServicesMaster().create_service(connection, data.model_dump())
        return "created", result

    def update_service(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        service_id = obj.pop("id", None)
        from services_catalog.services_modal import ServicesMaster
        from services_catalog.services_validator import UpdateServiceSchema
        data = UpdateServiceSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        ServicesMaster().update_service(connection, service_id, fields)
        return "success", {"message": "Service updated"}

    def delete_service(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        service_id = obj.pop("id", None)
        from services_catalog.services_modal import ServicesMaster
        ServicesMaster().delete_service(connection, service_id)
        return "success", {"message": "Service deleted"}

    def create_sub_category(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        service_id = obj.pop("id")
        from services_catalog.services_modal import ServicesMaster
        modal = ServicesMaster()
        data = CreateSubCategorySchema(**obj)
        sc_id = modal.create_sub_category(connection, service_id, data.name, data.sort_order)
        items_created = 0
        for item in (data.items or []):
            modal.create_sub_service(connection, sc_id, service_id, item.model_dump())
            items_created += 1
        return "created", {"sub_category_id": sc_id, "items_created": items_created}

    # ── NEW METHODS (additive) ───────────────────────────────────────

    def dashboard_overview(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        return "success", self.modal.dashboard_overview(connection)

    def list_bookings_detailed(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        status   = obj.get("status")
        page     = int(obj.get("page", 1))
        per_page = int(obj.get("per_page", 20))
        return "success", self.modal.list_bookings_detailed(connection, status, page, per_page)

    def list_users_detailed(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        page     = int(obj.get("page", 1))
        per_page = int(obj.get("per_page", 20))
        search   = obj.get("search")
        return "success", self.modal.list_users_detailed(connection, page, per_page, search)

    def list_all_services(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        return "success", self.modal.list_all_services(connection)

    def list_categories(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        return "success", self.modal.list_categories(connection)

    def list_payments(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        status   = obj.get("status")
        page     = int(obj.get("page", 1))
        per_page = int(obj.get("per_page", 20))
        return "success", self.modal.list_payments(connection, status, page, per_page)

    def list_reviews(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        rating   = obj.get("rating")
        page     = int(obj.get("page", 1))
        per_page = int(obj.get("per_page", 20))
        return "success", self.modal.list_reviews_detailed(connection, rating, page, per_page)

    def create_category(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        data   = CreateCategorySchema(**obj).model_dump()
        result = self.modal.create_category(connection, data)
        return "created", result

    def update_category(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        category_id = obj.pop("id")
        fields = UpdateCategorySchema(**obj).model_dump(exclude_none=True)
        self.modal.update_category(connection, category_id, fields)
        return "success", {"message": "Category updated"}

    def list_subscription_plans_all(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        page     = int(obj.get("page", 1))
        per_page = int(obj.get("per_page", 20))
        return "success", self.modal.list_subscription_plans_all(connection, page, per_page)

    def create_subscription_plan(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        data   = CreateSubscriptionPlanSchema(**obj).model_dump()
        result = self.modal.create_subscription_plan(connection, data)
        return "created", result

    def update_subscription_plan(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        plan_id = obj.pop("id")
        fields  = UpdateSubscriptionPlanSchema(**obj).model_dump(exclude_none=True)
        self.modal.update_subscription_plan(connection, plan_id, fields)
        return "success", {"message": "Plan updated"}

    def send_announcement(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        user_id = obj.pop("_user_id", None)
        data    = SendAnnouncementSchema(**obj).model_dump()
        data["sent_by"] = user_id

        self.modal.create_announcement(connection, data)

        from sqlalchemy import text
        target = data["target_role"]
        if target == "ALL":
            sql = "SELECT user_id FROM users WHERE role IN ('CUSTOMER','PROVIDER')"
            rows = connection.execute(text(sql)).fetchall()
        else:
            sql = "SELECT user_id FROM users WHERE role = :r"
            rows = connection.execute(text(sql), {"r": target}).fetchall()
        user_ids = [r[0] for r in rows]

        try:
            from notifications.notifications_service import NotificationsService
            NotificationsService().send_push(
                connection=connection,
                user_ids=user_ids,
                title=data["title"],
                body=data["body"],
            )
        except Exception:
            pass

        return "success", {"message": f"Announcement sent to {len(user_ids)} users"}

    def list_announcements(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        limit = int(obj.get("limit", 50))
        return "success", self.modal.list_announcements(connection, limit)

    def list_my_announcements(self, obj, connection):
        obj.pop("_user_id", None)
        role = obj.pop("_role", None) or "CUSTOMER"
        limit = int(obj.get("limit", 10))
        return "success", self.modal.list_announcements_for_role(connection, role, limit)

    def list_logs(self, obj, connection):
        self._require_admin(obj.pop("_role", None))
        obj.pop("_user_id", None)
        page     = int(obj.get("page", 1))
        per_page = int(obj.get("per_page", 20))
        search   = obj.get("search")
        return "success", self.modal.list_logs(connection, page, per_page, search)

    # ── LOGGING WRAPPERS ─────────────────────────────────────────────
    # Call the original untouched method, then write an activity log row.

    def approve_provider_logged(self, obj, connection):
        user_id     = obj.get("_user_id")
        provider_id = obj.get("id")
        from providers.providers_service import ProvidersService
        result = ProvidersService().admin_approve(obj, connection)
        try:
            self.modal.write_log(connection, user_id, "APPROVE_PROVIDER", "provider", provider_id)
        except Exception:
            pass
        return result

    def suspend_provider_logged(self, obj, connection):
        user_id     = obj.get("_user_id")
        provider_id = obj.get("id")
        from providers.providers_service import ProvidersService
        result = ProvidersService().admin_suspend(obj, connection)
        try:
            self.modal.write_log(connection, user_id, "SUSPEND_PROVIDER", "provider", provider_id)
        except Exception:
            pass
        return result

    def update_provider_bio_logged(self, obj, connection):
        user_id     = obj.get("_user_id")
        provider_id = obj.get("id")
        from providers.providers_service import ProvidersService
        result = ProvidersService().admin_update_bio(obj, connection)
        try:
            self.modal.write_log(connection, user_id, "UPDATE_PROVIDER_BIO", "provider", provider_id)
        except Exception:
            pass
        return result

    def delete_review_logged(self, obj, connection):
        user_id   = obj.get("_user_id")
        review_id = obj.get("id")
        from reviews.reviews_service import ReviewsService
        result = ReviewsService().admin_delete(obj, connection)
        try:
            self.modal.write_log(connection, user_id, "DELETE_REVIEW", "review", review_id)
        except Exception:
            pass
        return result
