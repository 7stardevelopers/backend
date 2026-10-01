import time
import unittest

import tests.helpers  # noqa: F401  (sets JWT_SECRET)
import jwt

from utilities.auth_tokens import decode_access_token, decode_refresh_token, get_jwt_secret
from request_handler import parse_request


def _token(**claims):
    claims.setdefault("exp", time.time() + 60)
    return jwt.encode(claims, get_jwt_secret(), algorithm="HS256")


class TokenTypeTests(unittest.TestCase):
    def test_access_token_accepted(self):
        payload = decode_access_token(_token(user_id="u", role="CUSTOMER", typ="access"))
        self.assertEqual(payload["user_id"], "u")

    def test_refresh_token_rejected_as_access(self):
        with self.assertRaises(PermissionError):
            decode_access_token(_token(user_id="u", role="ADMIN", typ="refresh", jti="j"))

    def test_legacy_refresh_token_rejected_as_access(self):
        with self.assertRaises(PermissionError):
            decode_access_token(_token(user_id="u", role="ADMIN", jti="j"))

    def test_access_token_rejected_as_refresh(self):
        with self.assertRaises(PermissionError):
            decode_refresh_token(_token(user_id="u", role="ADMIN", typ="access"))

    def test_empty_key_forgery_rejected(self):
        forged = jwt.encode({"user_id": "u", "role": "ADMIN", "exp": time.time() + 60}, "", algorithm="HS256")
        with self.assertRaises(PermissionError):
            decode_access_token(forged)


class RequestParsingTests(unittest.TestCase):
    def _event(self, body):
        return {"httpMethod": "POST", "path": "/x", "body": body, "headers": {}}

    def test_non_object_body_is_400(self):
        for body in ("[]", '"x"', "5"):
            with self.assertRaises(ValueError):
                parse_request(self._event(body))

    def test_invalid_json_is_400(self):
        with self.assertRaises(ValueError):
            parse_request(self._event("{not json"))

    def test_null_body_ok(self):
        self.assertEqual(parse_request(self._event("null"))["body"], {})


class RoutingAuthGateTests(unittest.TestCase):
    def setUp(self):
        import routing
        self.routing = routing

    def test_protected_route_requires_login(self):
        with self.assertRaises(PermissionError):
            self.routing.dispatch_rest("GET", "/instant-bookings/abc", {}, None, None, None)

    def test_places_requires_login(self):
        with self.assertRaises(PermissionError):
            self.routing.dispatch_rest("GET", "/places/autocomplete", {}, None, None, None)

    def test_unknown_route(self):
        with self.assertRaises(self.routing.RouteNotFound):
            self.routing.dispatch_rest("GET", "/nope", {}, None, "u", "CUSTOMER")

    def test_public_routes_exist(self):
        keys = {(m, p) for m, p, _, _ in self.routing.ROUTES}
        self.assertEqual([r for r in self.routing.PUBLIC_ROUTES if r not in keys], [])

    def test_negative_page_clamped(self):
        obj = {"page": "-5", "per_page": "100000"}
        self.routing._clamp_pagination(obj)
        self.assertEqual((obj["page"], obj["per_page"]), (1, 100))


if __name__ == "__main__":
    unittest.main()
