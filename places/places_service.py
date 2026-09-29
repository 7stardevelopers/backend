import os
import requests as http

PLACES_BASE  = "https://maps.googleapis.com/maps/api/place"
GEOCODE_BASE = "https://maps.googleapis.com/maps/api/geocode"


class PlacesService:
    def __init__(self):
        pass

    def autocomplete(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        input_text = (obj.get("input") or "").strip()
        if not input_text:
            raise ValueError("input is required")

        api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
        if not api_key:
            raise ValueError("Google Places API not configured")

        resp = http.get(
            f"{PLACES_BASE}/autocomplete/json",
            params={
                "input": input_text,
                "key": api_key,
                "language": "en",
                "components": "country:in",
            },
            timeout=5,
        )
        data = resp.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            raise ValueError(f"Places API error: {data.get('status')}")

        predictions = [
            {
                "place_id": p.get("place_id"),
                "description": p.get("description"),
                "main_text": p.get("structured_formatting", {}).get("main_text", ""),
            }
            for p in data.get("predictions", [])
        ]
        return "success", predictions

    def details(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        place_id = (obj.get("place_id") or "").strip()
        if not place_id:
            raise ValueError("place_id is required")

        api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
        if not api_key:
            raise ValueError("Google Places API not configured")

        resp = http.get(
            f"{PLACES_BASE}/details/json",
            params={"place_id": place_id, "fields": "geometry", "key": api_key},
            timeout=5,
        )
        data = resp.json()
        if data.get("status") != "OK":
            raise ValueError(f"Places API error: {data.get('status')}")

        loc = data.get("result", {}).get("geometry", {}).get("location", {})
        return "success", {"lat": loc.get("lat"), "lng": loc.get("lng")}

    def reverse_geocode(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        lat = obj.get("lat")
        lng = obj.get("lng")
        if not lat or not lng:
            raise ValueError("lat and lng are required")

        api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
        if not api_key:
            raise ValueError("Google Places API not configured")

        resp = http.get(
            f"{GEOCODE_BASE}/json",
            params={"latlng": f"{lat},{lng}", "key": api_key, "language": "en"},
            timeout=5,
        )
        data = resp.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            raise ValueError(f"Geocoding API error: {data.get('status')}")

        results = data.get("results", [])
        address = results[0].get("formatted_address", "Selected Location") if results else "Selected Location"
        return "success", {"address": address, "lat": float(lat), "lng": float(lng)}
