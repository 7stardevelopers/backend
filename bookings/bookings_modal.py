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
        obj["door_otp"] = f"{secrets.randbelow(10**4):04d}"
        obj["door_otp_generated_at"] = now_utc()
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

    def update_status(self, conn, booking_id: str, status: str, expected_status=None) -> dict:
        """Set status. With expected_status (str or tuple), only updates while the
        booking is still in that status, so concurrent requests can't overwrite
        each other (e.g. cancel racing accept, double-complete)."""
        upd = self.t.update().where(self.t.c.booking_id == booking_id)
        if expected_status is not None:
            allowed = (expected_status,) if isinstance(expected_status, str) else tuple(expected_status)
            upd = upd.where(self.t.c.status.in_(allowed))
        result = conn.execute(upd.values(status=status, updated_at=now_utc()))
        if expected_status is not None and result.rowcount == 0:
            raise ValueError("Booking status changed — please refresh and try again")
        return self.read_one(conn, booking_id)

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

    def claim_booking(self, conn, booking_id: str, provider_id: str) -> bool:
        """Atomically assign provider only if still unassigned. Returns True if claimed."""
        result = conn.execute(
            self.t.update()
            .where(self.t.c.booking_id == booking_id)
            .where(self.t.c.status == "PENDING")
            .where(self.t.c.provider_id == None)
            .values(provider_id=provider_id, status="ACCEPTED", updated_at=now_utc())
        )
        return result.rowcount > 0
