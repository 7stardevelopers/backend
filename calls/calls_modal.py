from utilities.db_connection import get_table
from utilities.common_table_elements import new_uuid, now_utc


class CallsMaster:
    @property
    def t(self):
        return get_table("call_logs")

    def create(self, conn, data: dict) -> dict:
        data["call_id"] = new_uuid()
        data["created_at"] = now_utc()
        data["updated_at"] = now_utc()
        conn.execute(self.t.insert().values(**data))
        return data

    def update_status_by_sid(self, conn, exotel_call_sid: str, status: str):
        conn.execute(
            self.t.update()
            .where(self.t.c.exotel_call_sid == exotel_call_sid)
            .values(status=status, updated_at=now_utc())
        )
