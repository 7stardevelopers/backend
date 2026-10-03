import os
import unittest
from unittest import mock

import tests.helpers  # noqa: F401  (sets JWT_SECRET before app imports)

from bookings import booking_eta
from utilities.redis_connection import _InMemoryRedis

ORIGIN = (17.7384, 83.3200)
DEST = (17.7200, 83.3000)


def _google_response(meters=3200, seconds=600, traffic=720, status="OK"):
    r = mock.Mock()
    r.json.return_value = {
        "status": status,
        "rows": [{"elements": [{
            "status": "OK",
            "distance": {"value": meters},
            "duration": {"value": seconds},
            "duration_in_traffic": {"value": traffic},
        }]}],
    }
    return r


class RoadEtaTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "k"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_google_road_distance_and_traffic_duration(self):
        with mock.patch.object(booking_eta.http, "get", return_value=_google_response()):
            eta = booking_eta.road_eta("b1", ORIGIN, DEST)
        self.assertEqual(eta, {"distance_km": 3.2, "duration_min": 12, "source": "google"})

    def test_falls_back_to_estimate_on_google_error(self):
        with mock.patch.object(booking_eta.http, "get", return_value=_google_response(status="REQUEST_DENIED")):
            eta = booking_eta.road_eta("b1", ORIGIN, DEST)
        self.assertEqual(eta["source"], "estimate")
        self.assertGreater(eta["distance_km"], 0)

    def test_cached_so_both_apps_see_same_value(self):
        cache = _InMemoryRedis()
        with mock.patch.object(booking_eta.http, "get", return_value=_google_response()) as get:
            first = booking_eta.road_eta("b1", ORIGIN, DEST, cache)
            second = booking_eta.road_eta("b1", (0.0, 0.0), DEST, cache)
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)

    def test_no_key_uses_estimate_without_calling_google(self):
        with mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "", "GOOGLE_MAPS_API_KEY": ""}), \
             mock.patch.object(booking_eta.http, "get") as get:
            eta = booking_eta.road_eta("b1", ORIGIN, DEST)
        get.assert_not_called()
        self.assertEqual(eta["source"], "estimate")


if __name__ == "__main__":
    unittest.main()
