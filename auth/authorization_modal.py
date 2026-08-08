from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class UsersMaster:
    @property
    def t(self):
        return get_table("users")

    @property
    def addr(self):
        return get_table("user_addresses")

    @property
    def refresh(self):
        return get_table("refresh_tokens")

    # ── users ─────────────────────────────────────────────────────────

    def find_by_phone(self, conn, phone: str):
        sel = self.t.select().where(self.t.c.phone == phone)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def find_by_id(self, conn, user_id: str):
        sel = self.t.select().where(self.t.c.user_id == user_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def create(self, conn, phone: str, role: str = "CUSTOMER"):
        user_id = new_uuid()
        ins = self.t.insert().values(
            user_id=user_id,
            phone=phone,
            role=role,
            status="ACTIVE",
            created_at=now_utc(),
            updated_at=now_utc(),
        )
        conn.execute(ins)
        return self.find_by_id(conn, user_id)

    def update(self, conn, user_id: str, fields: dict):
        fields["updated_at"] = now_utc()
        upd = self.t.update().where(self.t.c.user_id == user_id).values(**fields)
        conn.execute(upd)

    # ── addresses ─────────────────────────────────────────────────────

    def get_addresses(self, conn, user_id: str):
        sel = self.addr.select().where(self.addr.c.user_id == user_id).where(
            self.addr.c.is_deleted == False
        )
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def add_address(self, conn, user_id: str, data: dict):
        if data.get("is_default"):
            conn.execute(
                self.addr.update()
                .where(self.addr.c.user_id == user_id)
                .values(is_default=False)
            )
        data["address_id"] = new_uuid()
        data["user_id"] = user_id
        data["created_at"] = now_utc()
        conn.execute(self.addr.insert().values(**data))
        return data["address_id"]

    def update_address(self, conn, user_id: str, address_id: str, fields: dict):
        if fields.get("is_default"):
            conn.execute(
                self.addr.update()
                .where(self.addr.c.user_id == user_id)
                .values(is_default=False)
            )
        conn.execute(
            self.addr.update()
            .where(self.addr.c.address_id == address_id)
            .where(self.addr.c.user_id == user_id)
            .values(**fields)
        )

    def delete_address(self, conn, user_id: str, address_id: str):
        conn.execute(
            self.addr.update()
            .where(self.addr.c.address_id == address_id)
            .where(self.addr.c.user_id == user_id)
            .values(is_deleted=True)
        )

    # ── refresh tokens ────────────────────────────────────────────────

    def store_refresh_token(self, conn, user_id: str, jti: str, expires_at):
        ins = self.refresh.insert().values(
            token_id=new_uuid(),
            user_id=user_id,
            jti=jti,
            expires_at=expires_at,
            revoked=False,
            created_at=now_utc(),
        )
        conn.execute(ins)

    def revoke_refresh_token(self, conn, jti: str):
        upd = self.refresh.update().where(self.refresh.c.jti == jti).values(revoked=True)
        conn.execute(upd)

    def find_refresh_token(self, conn, jti: str):
        sel = self.refresh.select().where(self.refresh.c.jti == jti)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None
