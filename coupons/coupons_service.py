from coupons.coupons_modal import CouponsMaster
from bookings.booking_pricing import calculate_coupon_discount, check_coupon_eligibility
from coupons.coupons_validator import ValidateCouponSchema, CreateCouponSchema, UpdateCouponSchema


class CouponsService:
    def __init__(self):
        self.modal = CouponsMaster()

    def validate(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        data = ValidateCouponSchema(**obj)

        coupon = self.modal.find_by_code(connection, data.coupon_code)
        if not coupon:
            raise ValueError("Invalid coupon code")
        # Advisory only — the real check + reservation happens in booking creation.
        check_coupon_eligibility(connection, user_id, coupon, data.service_id, data.cart_total)
        discount = _calculate_discount(coupon, data.cart_total)
        return "success", {
            "coupon_id": coupon["coupon_id"],
            "code": coupon["code"],
            "title": coupon["title"],
            "discount": discount,
            "final_total": data.cart_total - discount,
        }

    def list_active(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        coupons = self.modal.list_active(connection)
        return "success", coupons

    def admin_create(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        data = CreateCouponSchema(**obj)
        coupon = self.modal.create(connection, data.model_dump())
        return "created", coupon

    def admin_delete(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        coupon_id = obj.get("id") or obj.get("coupon_id")
        self.modal.delete(connection, coupon_id)
        return "success", {"message": "Coupon deleted"}

    def admin_list_all(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        return "success", self.modal.list_all(connection)

    def admin_update(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        coupon_id = obj.pop("id")
        data = UpdateCouponSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        if fields:
            self.modal.update(connection, coupon_id, fields)
        return "success", {"message": "Coupon updated"}


def _calculate_discount(coupon: dict, cart_total: int) -> int:
    return calculate_coupon_discount(coupon, cart_total)
