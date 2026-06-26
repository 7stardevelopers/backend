import uuid
from datetime import datetime, timezone


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_utc():
    return datetime.now(timezone.utc)


def strip_private_keys(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if not k.startswith("_")}


def paginate(query, page: int = 1, per_page: int = 20):
    offset = (max(page, 1) - 1) * per_page
    return query.limit(per_page).offset(offset)
