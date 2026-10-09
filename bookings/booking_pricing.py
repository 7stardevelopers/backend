"""Server-side booking price calculation.

Every amount the customer is charged is derived here from database prices —
client-supplied sub_total / discount / total_amount are never trusted.

Order of application (mirrors Customer app create.jsx):
  sub_total  = Σ sub_service.price × qty   (or services.base_price when no items)
  coupon     → discount off sub_total
  subscription discount_pct → off sub_total (only while bookings_included remain)
  coins      → 1 coin = ₹1 (100 paise), capped at what's left
  total      = sub_total − coupon − subscription − coins×100   (never below 0, paise)
"""
from datetime import datetime, timezone
from sqlalchemy import text

from coupons.coupons_modal import CouponsMaster
from subscriptions.subscriptions_modal import SubscriptionsMaster
from utilities.db_connection import get_table

MAX_ITEM_QTY = 20
# Amounts are paise. One coin (referral bonus etc.) is worth ₹1.
COIN_VALUE_PAISE = 100


def price_booking(conn, user_id: str, service_id: str, items=None, coupon_id=None, coins_used=0) -> dict:
    services_t = get_table("services")
    service = conn.execute(
        services_t.select().where(services_t.c.service_id == service_id)
    ).mappings().fetchone()
    if not service or not service["is_active"]:
        raise ValueError("Service not available")

    priced_items = _price_items(conn, service_id, items or [])
    if priced_items:
        sub_total = sum(i["price"] * i["quantity"] for i in priced_items)
    else:
        sub_total = int(service["base_price"])
    if sub_total <= 0:
        raise ValueError("Booking total must be greater than zero")

    coupon = None
    coupon_discount = 0
    if coupon_id:
        coupon = _check_coupon(conn, user_id, coupon_id, service_id, sub_total)
        coupon_discount = calculate_coupon_discount(coupon, sub_total)

    subscription = None
    sub_discount = 0
    sub_modal = SubscriptionsMaster()
    active_sub = sub_modal.get_active_subscription(conn, user_id)
    if active_sub:
        plan = sub_modal.get_plan(conn, active_sub["plan_id"])
        included = plan.get("bookings_included") if plan else None
        has_quota = included is None or (active_sub.get("bookings_used") or 0) < included
        pct = min(100, max(0, int(plan.get("discount_pct") or 0))) if plan else 0
        if plan and has_quota:
            subscription = active_sub  # every booking under the plan uses quota
            sub_discount = int(sub_total * pct / 100)

    discount = min(sub_total, coupon_discount + sub_discount)
    after_discount = sub_total - discount

    coins = 0
    if coins_used:
        from referrals.referrals_modal import ReferralsMaster
        balance = ReferralsMaster().get_balance(conn, user_id)
        if coins_used > balance:
            raise ValueError("You don't have enough coins for this redemption")
        coins = min(int(coins_used), after_discount // COIN_VALUE_PAISE)

    return {
        "sub_total": sub_total,
        "discount": discount,
        "coins_used": coins,
        "total_amount": max(0, after_discount - coins * COIN_VALUE_PAISE),
        "items": priced_items,
        "coupon": coupon,
        "subscription": subscription,
    }


def apply_booking_side_effects(conn, user_id: str, booking_id: str, pricing: dict):
    """Reserve the coupon, consume subscription quota and debit coins.
    Any failure raises so the whole booking transaction rolls back."""
    coupon = pricing.get("coupon")
    if coupon:
        if not CouponsMaster().reserve_use(conn, coupon["coupon_id"], user_id, booking_id):
            raise ValueError("Coupon usage limit reached")

    sub = pricing.get("subscription")
    if sub:
        SubscriptionsMaster().increment_bookings_used(conn, sub["subscription_id"])

    if pricing.get("coins_used"):
        from referrals.referrals_modal import ReferralsMaster
        if not ReferralsMaster().debit(conn, user_id, pricing["coins_used"], "BOOKING_REDEMPTION", booking_id):
            raise ValueError("Could not redeem coins — balance changed. Please try again.")


def calculate_coupon_discount(coupon: dict, cart_total: int) -> int:
    if coupon["type"] == "FLAT":
        return max(0, min(int(coupon["value"]), cart_total))
    if coupon["type"] in ("PERCENT", "GPAY"):
        pct = min(100, max(0, int(coupon["value"])))
        discount = int(cart_total * pct / 100)
        if coupon.get("max_discount"):
            discount = min(discount, int(coupon["max_discount"]))
        return discount
    return 0


def check_coupon_eligibility(conn, user_id: str, coupon: dict, service_id: str, cart_total: int):
    """Raises ValueError if the coupon can't be used. Shared with /coupons/validate."""
    expires = coupon.get("expires_at")
    if expires is not None:
        now = datetime.utcnow() if expires.tzinfo is None else datetime.now(timezone.utc)
        if expires < now:
            raise ValueError("Coupon has expired")
    if coupon["max_uses"] is not None and coupon["used_count"] >= coupon["max_uses"]:
        raise ValueError("Coupon usage limit reached")
    if cart_total < (coupon.get("min_order_amount") or 0):
        raise ValueError(f"Minimum order required: ₹{coupon['min_order_amount'] // 100}")
    if coupon.get("service_ids") and service_id not in coupon["service_ids"]:
        raise ValueError("Coupon not valid for this service")
    if CouponsMaster().user_already_used(conn, user_id, coupon["coupon_id"]):
        raise ValueError("You've already used this coupon")


def _check_coupon(conn, user_id, coupon_id, service_id, sub_total):
    coupon = CouponsMaster().find_by_id(conn, coupon_id)
    if not coupon or not coupon.get("is_active"):
        raise ValueError("Invalid coupon")
    check_coupon_eligibility(conn, user_id, coupon, service_id, sub_total)
    return coupon


def _price_items(conn, service_id: str, items: list) -> list:
    """Look up each sub_service's real price. Unknown ids or ids belonging to a
    different service are skipped (the app sends the service id itself as the
    single item when no sub-services are chosen)."""
    wanted = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        ss_id = item.get("sub_service_id")
        if not ss_id:
            continue
        raw_qty = item.get("quantity", item.get("qty", 1))
        try:
            qty = int(1 if raw_qty is None else raw_qty)
        except (TypeError, ValueError):
            raise ValueError("Invalid item quantity")
        if qty < 1 or qty > MAX_ITEM_QTY:
            raise ValueError(f"Item quantity must be between 1 and {MAX_ITEM_QTY}")
        wanted[ss_id] = wanted.get(ss_id, 0) + qty
    if not wanted:
        return []

    rows = conn.execute(text("""
        SELECT sub_service_id, name, price
        FROM sub_services
        WHERE service_id = :sid AND is_active = TRUE AND sub_service_id IN :ids
    """).bindparams(_expanding("ids")), {"sid": service_id, "ids": list(wanted)}).mappings().fetchall()
    return [
        {"sub_service_id": r["sub_service_id"], "name": r["name"],
         "price": int(r["price"]), "quantity": wanted[r["sub_service_id"]]}
        for r in rows
    ]


def _expanding(name):
    from sqlalchemy import bindparam
    return bindparam(name, expanding=True)


def release_booking_side_effects(conn, booking: dict):
    """Undo apply_booking_side_effects when a booking is cancelled: give back the
    coupon use, the subscription booking and any coins spent."""
    booking_id = booking["booking_id"]
    user_id = booking["customer_id"]
    released = conn.execute(text(
        "DELETE FROM coupon_uses WHERE booking_id = :bid"
    ), {"bid": booking_id}).rowcount
    if released and booking.get("coupon_id"):
        conn.execute(text(
            "UPDATE coupons SET used_count = used_count - 1 WHERE coupon_id = :cid AND used_count > 0"
        ), {"cid": booking["coupon_id"]})

    spent = conn.execute(text("""
        SELECT COALESCE(-SUM(delta), 0) FROM wallet_ledger
        WHERE booking_id = :bid AND user_id = :uid AND reason IN ('BOOKING_REDEMPTION', 'BOOKING_REFUND')
    """), {"bid": booking_id, "uid": user_id}).scalar() or 0
    if spent > 0:
        from referrals.referrals_modal import ReferralsMaster
        ReferralsMaster().credit(conn, user_id, int(spent), "BOOKING_REFUND", booking_id)

    sub = SubscriptionsMaster().get_active_subscription(conn, user_id)
    if sub and booking.get("created_at") and sub.get("starts_at") and booking["created_at"] >= sub["starts_at"]:
        subs_t = get_table("user_subscriptions")
        conn.execute(
            subs_t.update()
            .where(subs_t.c.subscription_id == sub["subscription_id"])
            .where(subs_t.c.bookings_used > 0)
            .values(bookings_used=subs_t.c.bookings_used - 1)
        )
