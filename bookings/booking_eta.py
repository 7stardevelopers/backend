"""Road distance + ETA from the provider's live location to the booking address.

Both apps read the same cached value, so the customer and the worker always see
the same km / minutes. Google Distance Matrix (driving, live traffic) when the
key works; otherwise a straight-line estimate so the screen never goes blank.
"""
import json
import os

import requests as http

from providers.provider_matching import haversine

DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
CACHE_TTL_SECONDS = 30
# Fallback only: straight line × road factor at city speed (same as the customer app's old estimate).
ROAD_FACTOR = 1.35
CITY_KMPH = 20


def estimate_eta(origin, dest) -> dict:
    km = haversine(origin[0], origin[1], dest[0], dest[1]) * ROAD_FACTOR
    return {
        "distance_km": round(km, 1),
        "duration_min": max(1, round(km / CITY_KMPH * 60)),
        "source": "estimate",
    }


def google_eta(origin, dest, api_key: str) -> dict:
    resp = http.get(
        DISTANCE_MATRIX_URL,
        params={
            "origins": f"{origin[0]},{origin[1]}",
            "destinations": f"{dest[0]},{dest[1]}",
            "mode": "driving",
            "departure_time": "now",
            "key": api_key,
        },
        timeout=5,
    )
    data = resp.json()
    if data.get("status") != "OK":
        raise ValueError(f"Distance Matrix error: {data.get('status')}")
    el = data["rows"][0]["elements"][0]
    if el.get("status") != "OK":
        raise ValueError(f"Distance Matrix element: {el.get('status')}")
    seconds = (el.get("duration_in_traffic") or el["duration"])["value"]
    return {
        "distance_km": round(el["distance"]["value"] / 1000, 1),
        "duration_min": max(1, round(seconds / 60)),
        "source": "google",
    }


def road_eta(booking_id: str, origin, dest, redis_client=None) -> dict:
    """origin/dest are (lat, lng). Cached per booking for CACHE_TTL_SECONDS."""
    key = f"eta:{booking_id}"
    if redis_client is not None:
        try:
            cached = redis_client.get(key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            print(f"[ETA] cache read failed (non-fatal): {e}")

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_PLACES_API_KEY", "")
    result = None
    if api_key:
        try:
            result = google_eta(origin, dest, api_key)
        except Exception as e:
            print(f"[ETA] Google failed, using estimate (non-fatal): {e}")
    if result is None:
        result = estimate_eta(origin, dest)

    if redis_client is not None:
        try:
            redis_client.setex(key, CACHE_TTL_SECONDS, json.dumps(result))
        except Exception as e:
            print(f"[ETA] cache write failed (non-fatal): {e}")
    return result


DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
# The customer app re-requests only when the expert leaves the drawn route, so a
# short cache just absorbs bursts (both apps, reconnects) without hiding detours.
ROUTE_CACHE_TTL_SECONDS = 20


def google_route(origin, dest, api_key: str) -> dict:
    resp = http.get(
        DIRECTIONS_URL,
        params={
            "origin": f"{origin[0]},{origin[1]}",
            "destination": f"{dest[0]},{dest[1]}",
            "mode": "driving",
            "departure_time": "now",
            "key": api_key,
        },
        timeout=5,
    )
    data = resp.json()
    if data.get("status") != "OK" or not data.get("routes"):
        raise ValueError(f"Directions error: {data.get('status')}")
    route = data["routes"][0]
    leg = route["legs"][0]
    seconds = (leg.get("duration_in_traffic") or leg["duration"])["value"]
    return {
        "polyline": route["overview_polyline"]["points"],
        "distance_km": round(leg["distance"]["value"] / 1000, 1),
        "duration_min": max(1, round(seconds / 60)),
        "source": "google",
    }


def road_route(booking_id: str, origin, dest, redis_client=None) -> dict:
    """Encoded road polyline origin → dest, or {"polyline": None, …estimate} when
    Google is unavailable (the app then draws a straight line)."""
    key = f"route:{booking_id}"
    if redis_client is not None:
        try:
            cached = redis_client.get(key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            print(f"[Route] cache read failed (non-fatal): {e}")

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_PLACES_API_KEY", "")
    result = None
    if api_key:
        try:
            result = google_route(origin, dest, api_key)
        except Exception as e:
            print(f"[Route] Google failed, no polyline (non-fatal): {e}")
    if result is None:
        result = {"polyline": None, **estimate_eta(origin, dest)}

    if redis_client is not None:
        try:
            redis_client.setex(key, ROUTE_CACHE_TTL_SECONDS, json.dumps(result))
        except Exception as e:
            print(f"[Route] cache write failed (non-fatal): {e}")
    return result
