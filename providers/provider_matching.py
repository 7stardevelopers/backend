import math
from providers.providers_modal import ProvidersMaster


def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def score_provider(provider: dict, booking_lat: float, booking_lng: float) -> float:
    p_lat = provider.get("last_lat") or provider.get("lat")
    p_lng = provider.get("last_lng") or provider.get("lng")

    if p_lat is None or p_lng is None:
        proximity_score = 0
    else:
        dist = haversine(booking_lat, booking_lng, float(p_lat), float(p_lng))
        proximity_score = max(0, 1 - dist / 20) * 40

    rating = float(provider.get("avg_rating") or 0)
    rating_score = (rating / 5) * 30

    acc_rate = float(provider.get("acceptance_rate") or 1.0)
    acceptance_score = acc_rate * 20

    avg_resp = float(provider.get("avg_response_secs") or 30)
    response_score = max(0, 1 - avg_resp / 30) * 10

    return proximity_score + rating_score + acceptance_score + response_score


def match_provider(connection, booking: dict):
    service_id = booking.get("service_id")
    if not service_id:
        return None

    address_snapshot = booking.get("address_snapshot") or {}
    booking_lat = address_snapshot.get("lat") or 17.3850
    booking_lng = address_snapshot.get("lng") or 78.4867

    modal = ProvidersMaster()
    candidates = modal.get_available_for_service(connection, service_id)

    if not candidates:
        return None

    scored = [
        {"provider": p, "score": score_provider(p, float(booking_lat), float(booking_lng))}
        for p in candidates
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[0]["provider"]
