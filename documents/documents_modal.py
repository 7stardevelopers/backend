from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class DocumentsMaster:
    @property
    def d(self):
        return get_table("provider_documents")

    def create(self, conn, data: dict) -> dict:
        data["document_id"] = new_uuid()
        data["status"] = "PENDING"
        data["created_at"] = now_utc()
        conn.execute(self.d.insert().values(**data))
        return data

    def list_by_provider(self, conn, provider_id: str) -> list:
        sel = self.d.select().where(self.d.c.provider_id == provider_id).order_by(self.d.c.created_at.desc())
        rows = conn.execute(sel).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_one(self, conn, document_id: str):
        sel = self.d.select().where(self.d.c.document_id == document_id)
        row = conn.execute(sel).fetchone()
        return dict(row._mapping) if row else None

    def update(self, conn, document_id: str, fields: dict):
        conn.execute(self.d.update().where(self.d.c.document_id == document_id).values(**fields))

    def upsert_by_type(self, conn, provider_id: str, doc_type: str, file_url: str):
        conn.execute(self.d.delete().where(
            (self.d.c.provider_id == provider_id) & (self.d.c.doc_type == doc_type)
        ))
        return self.create(conn, {"provider_id": provider_id, "doc_type": doc_type, "file_url": file_url})
