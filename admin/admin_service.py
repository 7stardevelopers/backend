from admin.admin_modal import AdminMaster
from admin.admin_validator import UpdateBookingSchema, CreateServiceAdminSchema, CreateSubCategorySchema


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
