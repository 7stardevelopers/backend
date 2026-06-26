from datetime import datetime, timezone
from coupons.coupons_modal import CouponsMaster
from coupons.coupons_validator import ValidateCouponSchema, CreateCouponSchema


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
        if coupon["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise ValueError("Coupon has expired")
        if coupon["used_count"] >= coupon["max_uses"]:
            raise ValueError("Coupon usage limit reached")
        if data.cart_total < coupon["min_order_amount"]:
            raise ValueError(f"Minimum order required: ₹{coupon['min_order_amount']//100}")
        if coupon.get("service_ids") and data.service_id not in coupon["service_ids"]:
            raise ValueError("Coupon not valid for this service")
        if self.modal.user_already_used(connection, user_id, coupon["coupon_id"]):
            raise ValueError("You've already used this coupon")

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


def _calculate_discount(coupon: dict, cart_total: int) -> int:
    if coupon["type"] == "FLAT":
        return min(coupon["value"], cart_total)
    elif coupon["type"] in ("PERCENT", "GPAY"):
        discount = int(cart_total * coupon["value"] / 100)
        if coupon.get("max_discount"):
            discount = min(discount, coupon["max_discount"])
        return discount
    return 0
