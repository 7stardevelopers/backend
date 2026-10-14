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

    def slots(self, obj, connection):
        """GET /services/{id}/slots?date=YYYY-MM-DD[&lat=&lng=] — what the booking screen
        may offer: Now (instant) and the day's time slots (IST)."""
        from datetime import datetime, timedelta
        from bookings import dispatch_rules as rules
        service_id = obj.get("id") or obj.get("service_id")
        row = connection.execute(self.modal.svcs.select().where(self.modal.svcs.c.service_id == service_id)).fetchone()
        if not row:
            raise ValueError(f"Service {service_id} not found")
        service = dict(row._mapping)
        now = datetime.now(rules.IST)
        day = obj.get("date") or now.date().isoformat()
        try:
            day_d = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")
        instant = bool(service.get("instant_available")) and bool(service.get("is_active", True))
        reason = None
        if not instant:
            reason = "Instant booking isn't available for this service"
        elif not rules.instant_open():
            instant, reason = False, rules.instant_closed_message()
        workers = None
        if instant and obj.get("lat") is not None and obj.get("lng") is not None:
            workers = _workers_within(connection, service_id, obj.get("lat"), obj.get("lng"),
                                      rules.INSTANT_ROUNDS[-1][1])
            if workers == 0:
                instant, reason = False, "No experts are free near you right now. Please schedule a time."
        last_day = (now + timedelta(days=rules.MAX_ADVANCE_DAYS)).date()
        return "success", {
            "service_id": service_id,
            "date": day_d.isoformat(),
            "instant_available": instant,
            "instant_reason": reason,
            "instant_eta_min": rules.INSTANT_ETA_MIN,
            "workers_nearby": workers,
            "min_lead_min": rules.MIN_LEAD_MIN,
            "first_date": now.date().isoformat(),
            "last_date": last_day.isoformat(),
            "slots": rules.slots_for(day_d) if now.date() <= day_d <= last_day else [],
        }

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


def _workers_within(connection, service_id, lat, lng, km):
    """Available workers for the service within km of the point (unknown location counts as near)."""
    from providers.provider_matching import haversine
    from providers.providers_modal import ProvidersMaster
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    count = 0
    for p in ProvidersMaster().get_available_for_service(connection, service_id):
        if p.get("last_lat") is None or p.get("last_lng") is None \
                or haversine(lat, lng, float(p["last_lat"]), float(p["last_lng"])) <= km:
            count += 1
    return count


def _bust_service_cache():
    try:
        r = get_redis()
        for key in r.scan_iter("cache:service*") or []:
            r.delete(key)
        r.delete("cache:categories")
    except Exception:
        pass
