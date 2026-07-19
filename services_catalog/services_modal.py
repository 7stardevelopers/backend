from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class ServicesMaster:
    @property
    def cats(self):
        return get_table("categories")

    @property
    def svcs(self):
        return get_table("services")

    @property
    def sub_cats(self):
        return get_table("sub_categories")

    @property
    def sub_svcs(self):
        return get_table("sub_services")

    def list_categories(self, conn):
        sel = self.cats.select().where(self.cats.c.is_active == True).order_by(self.cats.c.sort_order)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_services(self, conn, category_id=None):
        sel = self.svcs.select().where(self.svcs.c.is_active == True).order_by(self.svcs.c.sort_order)
        if category_id:
            sel = sel.where(self.svcs.c.category_id == category_id)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_categories_admin(self, conn):
        sel = self.cats.select().order_by(self.cats.c.sort_order)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def list_services_admin(self, conn, category_id=None):
        sel = self.svcs.select().order_by(self.svcs.c.sort_order)
        if category_id:
            sel = sel.where(self.svcs.c.category_id == category_id)
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_service_detail(self, conn, service_id: str):
        sel = self.svcs.select().where(self.svcs.c.service_id == service_id)
        row = conn.execute(sel).fetchone()
        if not row:
            raise ValueError(f"Service {service_id} not found")
        service = dict(row._mapping)

        sub_cat_sel = self.sub_cats.select().where(
            self.sub_cats.c.service_id == service_id
        ).order_by(self.sub_cats.c.sort_order)
        sub_cats = conn.execute(sub_cat_sel).fetchall()

        result_cats = []
        for sc in sub_cats:
            sc_dict = dict(sc._mapping)
            sub_svc_sel = self.sub_svcs.select().where(
                self.sub_svcs.c.sub_category_id == sc_dict["sub_category_id"]
            ).where(self.sub_svcs.c.is_active == True)
            sub_svcs = conn.execute(sub_svc_sel).fetchall()
            sc_dict["items"] = [dict(s._mapping) for s in sub_svcs]
            result_cats.append(sc_dict)

        service["sub_categories"] = result_cats
        return service

    def search_services(self, conn, query: str):
        from sqlalchemy import or_
        q = f"%{query}%"
        sel = self.svcs.select().where(
            self.svcs.c.is_active == True
        ).where(
            or_(
                self.svcs.c.name.ilike(q),
                self.svcs.c.description.ilike(q),
            )
        )
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def create_service(self, conn, data: dict):
        data["service_id"] = new_uuid()[:8]
        conn.execute(self.svcs.insert().values(**data))
        return data

    def create_category(self, conn, data: dict):
        data["category_id"] = new_uuid()[:8]
        conn.execute(self.cats.insert().values(**data))
        return data

    def update_service(self, conn, service_id: str, fields: dict):
        conn.execute(self.svcs.update().where(self.svcs.c.service_id == service_id).values(**fields))

    def delete_service(self, conn, service_id: str):
        conn.execute(self.svcs.update().where(self.svcs.c.service_id == service_id).values(is_active=False))

    def create_sub_category(self, conn, service_id: str, name: str, sort_order: int = 0) -> str:
        sc_id = new_uuid()
        conn.execute(self.sub_cats.insert().values(
            sub_category_id=sc_id,
            service_id=service_id,
            name=name,
            sort_order=sort_order,
        ))
        return sc_id

    def create_sub_service(self, conn, sub_category_id: str, service_id: str, data: dict) -> str:
        ss_id = new_uuid()
        conn.execute(self.sub_svcs.insert().values(
            sub_service_id=ss_id,
            sub_category_id=sub_category_id,
            service_id=service_id,
            name=data["name"],
            description=data.get("description"),
            price=int(data["price"]),
            duration_minutes=data.get("duration_minutes"),
            is_active=True,
        ))
        return ss_id
