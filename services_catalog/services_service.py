import json
from services_catalog.services_modal import ServicesMaster
from services_catalog.services_validator import CreateServiceSchema, UpdateServiceSchema
from utilities.redis_connection import get_redis

CACHE_TTL = 600


class ServicesService:
    def __init__(self):
        self.modal = ServicesMaster()

    def list_categories(self, obj, connection):
        r = get_redis()
        cached = r.get("cache:categories")
        if cached:
            return "success", json.loads(cached)
        cats = self.modal.list_categories(connection)
        r.setex("cache:categories", CACHE_TTL, json.dumps(cats, default=str))
        return "success", cats

    def list_services(self, obj, connection):
        category_id = obj.get("categoryId") or obj.get("category_id")
        cache_key = f"cache:services:{category_id or 'all'}"
        r = get_redis()
        cached = r.get(cache_key)
        if cached:
            return "success", json.loads(cached)
        svcs = self.modal.list_services(connection, category_id)
        r.setex(cache_key, CACHE_TTL, json.dumps(svcs, default=str))
        return "success", svcs

    def get_detail(self, obj, connection):
        service_id = obj.get("id") or obj.get("service_id")
        if not service_id:
            raise ValueError("service id required")
        cache_key = f"cache:service:{service_id}"
        r = get_redis()
        cached = r.get(cache_key)
        if cached:
            return "success", json.loads(cached)
        detail = self.modal.get_service_detail(connection, service_id)
        r.setex(cache_key, CACHE_TTL, json.dumps(detail, default=str))
        return "success", detail

    def search(self, obj, connection):
        q = obj.get("q", "").strip()
        if not q:
            raise ValueError("Search query required")
        results = self.modal.search_services(connection, q)
        return "success", results

    def admin_create(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        data = CreateServiceSchema(**obj)
        result = self.modal.create_service(connection, data.model_dump())
        _bust_service_cache()
        return "created", result

    def admin_update(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        service_id = obj.pop("id", None) or obj.pop("service_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        data = UpdateServiceSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        self.modal.update_service(connection, service_id, fields)
        _bust_service_cache()
        return "success", {"message": "Service updated"}

    def admin_delete(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        service_id = obj.pop("id", None) or obj.pop("service_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        self.modal.delete_service(connection, service_id)
        _bust_service_cache()
        return "success", {"message": "Service deleted"}


def _bust_service_cache():
    try:
        r = get_redis()
        for key in r.scan_iter("cache:service*") or []:
            r.delete(key)
        r.delete("cache:categories")
    except Exception:
        pass
