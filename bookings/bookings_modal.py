import json
import math
import secrets
from sqlalchemy import text, bindparam, or_
from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lng2 - lng1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class BookingsMaster:
    @property
    def t(self):
        return get_table("bookings")

    @property
    def items(self):
        return get_table("booking_items")

    def create(self, conn, obj: dict) -> dict:
        obj["booking_id"] = new_uuid()
        # No door OTP yet: it is generated only when the customer confirms the
        # worker's face at the door (identity_reports.door_check).
        obj["created_at"] = now_utc()
        obj["updated_at"] = now_utc()
        conn.execute(self.t.insert().values(**obj))
        return obj

    def read(self, conn, filters: dict = None, limit: int = 50, offset: int = 0) -> list:
        sel = self.t.select().order_by(self.t.c.created_at.desc()).limit(limit).offset(offset)
        if filters:
            for k, v in filters.items():
                if hasattr(self.t.c, k):
                    sel = sel.where(getattr(self.t.c, k) == v)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def read_one(self, conn, booking_id: str) -> dict:
        sel = self.t.select().where(self.t.c.booking_id == booking_id)
        row = conn.execute(sel).fetchone()
        if not row:
            raise ValueError(f"Booking {booking_id} not found")
        return dict(row._mapping)

    def update_status(self, conn, booking_id: str, status: str, expected_status=None, only_unpaid=False) -> dict:
        """Set status. With expected_status (str or tuple), only updates while the
        booking is still in that status, so concurrent requests can't overwrite
        each other (e.g. cancel racing accept, double-complete)."""
        upd = self.t.update().where(self.t.c.booking_id == booking_id)
        if expected_status is not None:
            allowed = (expected_status,) if isinstance(expected_status, str) else tuple(expected_status)
            upd = upd.where(self.t.c.status.in_(allowed))
        if only_unpaid:
            upd = upd.where(or_(self.t.c.payment_status != "PAID", self.t.c.payment_status.is_(None)))
        result = conn.execute(upd.values(status=status, updated_at=now_utc()))
        if expected_status is not None and result.rowcount == 0:
            raise ValueError("Booking status changed — please refresh and try again")
        return self.read_one(conn, booking_id)

    # ── Two-sided completion: worker AND customer must tap "Done" ─────────

    def mark_done(self, conn, booking_id: str, side: str) -> bool:
        """Record one side's "Done" (side = 'provider' | 'customer'). Only while
        IN_PROGRESS and only the first tap counts. Returns True if it was recorded."""
        col = {"provider": self.t.c.provider_done_at, "customer": self.t.c.customer_done_at}[side]
        result = conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .where(self.t.c.status == "IN_PROGRESS")
            .where(col.is_(None))
            .values({col.name: now_utc(), "updated_at": now_utc()})
        )
        return result.rowcount == 1

    def try_finish(self, conn, booking_id: str) -> bool:
        """IN_PROGRESS → COMPLETED once both sides are done and nothing is disputed.
        Conditional, so if both tap at the same moment exactly one call wins."""
        result = conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .where(self.t.c.status == "IN_PROGRESS")
            .where(self.t.c.provider_done_at.isnot(None))
            .where(self.t.c.customer_done_at.isnot(None))
            .where(self.t.c.completion_disputed_at.is_(None))
            .values(status="COMPLETED", updated_at=now_utc())
        )
        return result.rowcount == 1

    def mark_disputed(self, conn, booking_id: str) -> bool:
        result = conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .where(self.t.c.status == "IN_PROGRESS")
            .where(self.t.c.completion_disputed_at.is_(None))
            .values(completion_disputed_at=now_utc(), updated_at=now_utc())
        )
        return result.rowcount == 1

    def verify_door_otp(self, conn, booking_id: str, otp: str) -> bool:
        """Atomically mark the OTP verified and start the job. Only succeeds once,
        from ACCEPTED/EN_ROUTE, with the right OTP."""
        result = conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .where(self.t.c.door_otp == otp)
            .where(self.t.c.status.in_(("ACCEPTED", "EN_ROUTE")))
            .where(or_(self.t.c.door_otp_verified == False, self.t.c.door_otp_verified == None))
            .values(door_otp_verified=True, status="IN_PROGRESS", updated_at=now_utc())
        )
        return result.rowcount > 0

    def record_failed_otp_attempt(self, conn, booking_id: str) -> int:
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(otp_attempt_count=self.t.c.otp_attempt_count + 1, updated_at=now_utc())
        )
        row = conn.execute(
            self.t.select().where(self.t.c.booking_id == booking_id)
        ).fetchone()
        return row._mapping["otp_attempt_count"] if row else 0

    def record_failed_otp_attempt_committed(self, booking_id: str) -> int:
        """Increment the counter in its own transaction so it survives the
        rollback of the request that raised 'Invalid door OTP'."""
        from utilities.db_connection import get_engine
        with get_engine().begin() as own:
            return self.record_failed_otp_attempt(own, booking_id)

    def confirm_identity(self, conn, booking_id: str):
        conn.execute(
            self.t.update().where(self.t.c.booking_id == booking_id)
            .values(identity_confirmed_at=now_utc(), updated_at=now_utc())
        )

    def mark_identity_mismatch(self, conn, booking_id: str):
        """Customer says it's not the worker in the photo: withdraw any door OTP
        so nobody can start the job until admin sorts it out."""
        conn.execute(
            self.t.update().where(self.t.c.booking_id == booking_id)
            .values(identity_mismatch_at=now_utc(), identity_confirmed_at=None,
                    door_otp=None, door_otp_generated_at=None, updated_at=now_utc())
        )

    def regenerate_door_otp(self, conn, booking_id: str) -> str:
        new_otp = f"{secrets.randbelow(10**4):04d}"
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(
                door_otp=new_otp,
                door_otp_verified=False,
                otp_attempt_count=0,
                door_otp_generated_at=now_utc(),
                updated_at=now_utc(),
            )
        )
        return new_otp

    def create_items(self, conn, booking_id: str, items: list):
        """items come from booking_pricing.price_booking — already validated
        against sub_services with server-side prices."""
        for item in items:
            conn.execute(self.items.insert().values(
                booking_id=booking_id,
                sub_service_id=item["sub_service_id"],
                name_snapshot=item["name"],
                price_snapshot=item["price"],
                quantity=item["quantity"],
            ))

    def get_items(self, conn, booking_id: str) -> list:
        sel = self.items.select().where(self.items.c.booking_id == booking_id)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def mark_paid(self, conn, booking_id: str, payment_id: str) -> bool:
        """Booking → PAID only if not already paid (by another order) and still live."""
        result = conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            # Never over a paid or refunded booking — that payment is a duplicate.
            .where(or_(self.t.c.payment_status.in_(("PENDING", "FAILED")), self.t.c.payment_status.is_(None)))
            .where(self.t.c.status.notin_(("CANCELLED", "REJECTED")))
            .values(payment_id=payment_id, payment_status="PAID", updated_at=now_utc())
        )
        return result.rowcount == 1

    def claim_earning_credit(self, conn, booking_id: str) -> bool:
        """The worker's share is credited once: completed AND paid online."""
        result = conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .where(self.t.c.status == "COMPLETED")
            # paid online, or nothing to pay (fully covered by coupon/coins/plan)
            .where(or_(self.t.c.payment_status.in_(("PAID", "PARTIALLY_REFUNDED")), self.t.c.total_amount == 0))
            .where(self.t.c.provider_id.isnot(None))
            .where(self.t.c.earning_credited_at.is_(None))
            .values(earning_credited_at=now_utc())
        )
        return result.rowcount == 1

    def update_payment(self, conn, booking_id: str, payment_id: str, payment_status: str):
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(payment_id=payment_id, payment_status=payment_status, updated_at=now_utc())
        )

    def update_proof_photos(self, conn, booking_id: str, photo_urls: list):
        conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .values(proof_photos=photo_urls, updated_at=now_utc())
        )

    def get_available_for_provider(self, conn, provider_id: str, lat=None, lng=None) -> list:
        # Pull coords from snapshot first, fall back to user_addresses table
        rows = conn.execute(text("""
            SELECT b.*,
                   COALESCE(
                       JSON_UNQUOTE(JSON_EXTRACT(b.address_snapshot, '$.lat')),
                       ua.lat
                   ) AS _lat,
                   COALESCE(
                       JSON_UNQUOTE(JSON_EXTRACT(b.address_snapshot, '$.lng')),
                       ua.lng
                   ) AS _lng
            FROM bookings b
            JOIN provider_services ps ON b.service_id = ps.service_id
            LEFT JOIN user_addresses ua ON ua.address_id = b.address_id
            WHERE b.status = 'PENDING'
              AND b.provider_id IS NULL
              -- only paid (or fully covered) jobs reach workers
              AND (b.payment_status = 'PAID' OR b.total_amount = 0)
              AND ps.provider_id = :pid
              -- never offer a worker their own bookings (made before they registered)
              AND b.customer_id <> (SELECT user_id FROM providers WHERE provider_id = :pid)
            ORDER BY b.scheduled_at ASC
            LIMIT 50
        """), {"pid": provider_id})
        bookings = [dict(r._mapping) for r in rows.fetchall()]

        if lat is None or lng is None:
            # No worker coords — strip internal fields and return all
            for b in bookings:
                b.pop("_lat", None)
                b.pop("_lng", None)
            return bookings[:20]

        result = []
        for b in bookings:
            b_lat = b.pop("_lat", None)
            b_lng = b.pop("_lng", None)
            if b_lat is None or b_lng is None:
                # No location data anywhere — skip, can't verify proximity
                continue
            if _haversine_km(lat, lng, float(b_lat), float(b_lng)) <= 20:
                result.append(b)
        return result[:20]

    def read_for_provider(self, conn, provider_id: str, status_filter=None, limit=20, offset=0) -> list:
        params = {"pid": provider_id, "limit": limit, "offset": offset}
        status_clause = ""
        if status_filter:
            status_clause = "AND b.status = :status"
            params["status"] = status_filter
        sql = text(f"""
            SELECT b.*, u.name AS customer_name, u.photo_url AS customer_photo
            FROM bookings b
            JOIN users u ON u.user_id = b.customer_id
            WHERE b.provider_id = :pid
            {status_clause}
            ORDER BY b.created_at DESC
            LIMIT :limit OFFSET :offset
        """)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_past_providers(self, conn, customer_id: str, service_id: str, limit: int = 5) -> list:
        sql = text("""
            SELECT b.provider_id, MAX(b.created_at) AS last_booked
            FROM bookings b
            WHERE b.customer_id = :cid AND b.service_id = :sid AND b.status = 'COMPLETED'
              AND b.provider_id IS NOT NULL
            GROUP BY b.provider_id
            ORDER BY last_booked DESC
            LIMIT :lim
        """)
        provider_ids = [r["provider_id"] for r in conn.execute(
            sql, {"cid": customer_id, "sid": service_id, "lim": limit}
        ).mappings().fetchall()]
        if not provider_ids:
            return []
        detail_sql = text("""
            SELECT p.provider_id, u.name, u.photo_url, p.avg_rating, p.is_available
            FROM providers p JOIN users u ON u.user_id = p.user_id
            WHERE p.provider_id IN :ids
        """).bindparams(bindparam("ids", expanding=True))
        rows = conn.execute(detail_sql, {"ids": provider_ids}).mappings().fetchall()
        by_id = {r["provider_id"]: dict(r) for r in rows}
        # preserve the most-recently-booked-first order from the first query
        return [by_id[pid] for pid in provider_ids if pid in by_id]

    def attach_provider_summary(self, conn, bookings: list) -> list:
        """Adds provider_name / provider_photo / provider_rating to each booking
        (in place) — the customer's lists show the expert's face like the detail does."""
        provider_ids = list({b["provider_id"] for b in bookings if b.get("provider_id")})
        if not provider_ids:
            return bookings
        sql = text("""
            SELECT p.provider_id, u.name, u.photo_url, p.avg_rating
            FROM providers p JOIN users u ON u.user_id = p.user_id
            WHERE p.provider_id IN :ids
        """).bindparams(bindparam("ids", expanding=True))
        by_id = {r["provider_id"]: r for r in conn.execute(sql, {"ids": provider_ids}).mappings().fetchall()}
        for b in bookings:
            row = by_id.get(b.get("provider_id"))
            if row:
                b["provider_name"] = row["name"]
                b["provider_photo"] = row["photo_url"]
                b["provider_rating"] = float(row["avg_rating"] or 0)
        return bookings

    def claim_booking(self, conn, booking_id: str, provider_id: str) -> bool:
        """Atomically assign provider only if still unassigned. Returns True if claimed."""
        result = conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .where(self.t.c.status == "PENDING")
            .where(self.t.c.provider_id == None)
            .where(or_(self.t.c.payment_status == "PAID", self.t.c.total_amount == 0))
            .values(provider_id=provider_id, status="ACCEPTED", updated_at=now_utc())
        )
        return result.rowcount > 0
