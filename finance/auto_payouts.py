"""
Scheduled payouts (Rapido-style "redeem twice a week"). Run by location_trigger
every 15 minutes; acts once per PAYOUT_DAYS day (IST) from PAYOUT_HOUR_IST on.

Every approved worker with at least PAYOUT_MIN available (wallet minus payouts
already in flight) gets a payout_requests row in APPROVED. Admin transfers the
money from the bank and marks it PROCESSED in the finance queue, which debits
the wallet (finance_service.approve_payout).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from payments.money_rules import PAYOUT_DAYS, PAYOUT_HOUR_IST, PAYOUT_MIN
from utilities.common_table_elements import new_uuid, now_utc

IST = timezone(timedelta(hours=5, minutes=30))
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
AUTO_NOTE = "AUTO"


def _now_ist(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(IST)


def next_payout_date(now=None):
    """Next IST date payouts are created (today if today's batch hasn't run its hour yet)."""
    today = _now_ist(now)
    for offset in range(8):
        day = today + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] in PAYOUT_DAYS and (offset > 0 or today.hour < PAYOUT_HOUR_IST):
            return day.date()
    return today.date()


def run_payout_batch(conn, now=None, notif=None) -> int:
    """Queue today's payouts. Returns how many were created (0 = not a payout day / already ran)."""
    ist = _now_ist(now)
    if WEEKDAYS[ist.weekday()] not in PAYOUT_DAYS or ist.hour < PAYOUT_HOUR_IST:
        return 0
    note = f"{AUTO_NOTE} {ist.date().isoformat()}"
    if conn.execute(text("SELECT 1 FROM payout_requests WHERE notes = :n LIMIT 1"), {"n": note}).fetchone():
        return 0  # today's batch already ran

    lock = "" if conn.dialect.name == "sqlite" else " FOR UPDATE"
    providers = conn.execute(text(
        "SELECT provider_id, user_id, wallet_balance, bank_account_number, bank_ifsc "
        "FROM providers WHERE status = 'APPROVED' AND wallet_balance >= :min" + lock
    ), {"min": PAYOUT_MIN}).mappings().fetchall()

    if notif is None:
        from notifications.notifications_service import NotificationsService
        notif = NotificationsService()

    created = 0
    for p in providers:
        reserved = conn.execute(text(
            "SELECT COALESCE(SUM(amount), 0) FROM payout_requests "
            "WHERE provider_id = :pid AND status IN ('PENDING', 'APPROVED')"
        ), {"pid": p["provider_id"]}).scalar() or 0
        available = int(p["wallet_balance"] or 0) - int(reserved)
        if available < PAYOUT_MIN:
            continue
        if not p.get("bank_account_number") or not p.get("bank_ifsc"):
            _push(notif, conn, p["user_id"], "Add your bank details",
                  f"₹{available // 100} is ready to be paid out. Add your bank account in Profile to receive it.")
            continue
        conn.execute(text(
            "INSERT INTO payout_requests (payout_id, provider_id, amount, status, bank_account, bank_ifsc, notes, created_at) "
            "VALUES (:id, :pid, :amt, 'APPROVED', :acc, :ifsc, :note, :at)"
        ), {"id": new_uuid(), "pid": p["provider_id"], "amt": available, "acc": p["bank_account_number"],
            "ifsc": p["bank_ifsc"], "note": note, "at": now_utc()})
        created += 1
        _push(notif, conn, p["user_id"], "Payout scheduled",
              f"₹{available // 100} is on its way to your bank account.")
    print(f"[Payouts] {note}: {created} payout(s) queued")
    return created


def _push(notif, conn, user_id, title, body):
    try:
        notif.send_push(connection=conn, user_ids=[user_id], title=title, body=body,
                        data={"type": "payout_update"})
    except Exception as e:
        print(f"[Payouts] push failed (non-fatal): {e}")
