from utilities.db_connection import get_table


class CustomersService:
    """Reciprocal to ProvidersService.get_public_profile — lets a provider
    view the basic profile of a customer they have an active/recent booking
    relationship with. No modal/validator file: this is a single read-only
    query, not a full CRUD resource.
    """

    def get_public_profile(self, obj, connection):
        provider_user_id = obj.pop("_user_id", None)
        role = obj.pop("_role", None)
        customer_id = obj.get("id") or obj.get("customer_id")

        users_t = get_table("users")
        bookings_t = get_table("bookings")

        if role not in ("ADMIN", "SUPPORT"):
            if role != "PROVIDER":
                raise PermissionError("Provider role required")
            providers_t = get_table("providers")
            provider_row = connection.execute(
                providers_t.select().where(providers_t.c.user_id == provider_user_id)
            ).mappings().fetchone()
            if not provider_row:
                raise PermissionError("Provider profile not found")
            has_relationship = connection.execute(
                bookings_t.select()
                .where(bookings_t.c.provider_id == provider_row["provider_id"])
                .where(bookings_t.c.customer_id == customer_id)
                .where(bookings_t.c.status.in_(["ACCEPTED", "EN_ROUTE", "IN_PROGRESS", "COMPLETED"]))
            ).fetchone()
            if not has_relationship:
                raise PermissionError("No booking relationship with this customer")

        row = connection.execute(
            users_t.select().where(users_t.c.user_id == customer_id)
        ).mappings().fetchone()
        if not row:
            raise ValueError("Customer not found")

        return "success", {
            "user_id": row["user_id"],
            "name": row["name"],
            "photo_url": row["photo_url"],
            "member_since": row["created_at"],
        }
