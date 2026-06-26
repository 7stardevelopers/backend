from utilities.db_connection import metadata
from utilities.common_table_elements import new_uuid, now_utc


class ServicesMaster:
    def __init__(self):
        self.cats = metadata.tables["categories"]
        self.svcs = metadata.tables["services"]
        self.sub_cats = metadata.tables["sub_categories"]
        self.sub_svcs = metadata.tables["sub_services"]

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

    def update_service(self, conn, service_id: str, fields: dict):
        conn.execute(self.svcs.update().where(self.svcs.c.service_id == service_id).values(**fields))

    def delete_service(self, conn, service_id: str):
        conn.execute(self.svcs.update().where(self.svcs.c.service_id == service_id).values(is_active=False))
