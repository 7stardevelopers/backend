import base64
import json
from urllib.parse import parse_qsl

from utilities.auth_tokens import decode_access_token


def parse_request(event):
    method = event.get("httpMethod", "GET").upper()

    raw_path = event.get("path", "/")
    path = _normalize_path(raw_path, event.get("pathParameters") or {})

    headers = event.get("headers") or {}
    body = _parse_body(event.get("body"), event.get("isBase64Encoded"), _header(headers, "content-type"))

    query = event.get("queryStringParameters") or {}
    body.update({k: v for k, v in query.items() if k not in body})

    user_id, role = _extract_jwt(headers) if not raw_path.rstrip("/").endswith("/payments/webhook") else (None, None)

    req = {"method": method, "path": path, "body": body, "user_id": user_id, "role": role}
    if path == "/payments/webhook":
        # Razorpay signs the exact bytes it sent — keep them for the HMAC check.
        raw = event.get("body") or ""
        if event.get("isBase64Encoded") and raw:
            raw = base64.b64decode(raw).decode("utf-8")
        req["raw_body"] = raw
        req["rzp_signature"] = _header(headers, "x-razorpay-signature")
    return req


def _header(headers, name):
    for k, v in headers.items():
        if k.lower() == name:
            return v or ""
    return ""


def _parse_body(raw_body, is_base64, content_type):
    """JSON by default; also form-encoded (webhooks such as Exotel's status
    callback default to application/x-www-form-urlencoded)."""
    if raw_body is None or raw_body == "":
        return {}
    if not isinstance(raw_body, str):
        body = raw_body
    else:
        if is_base64:
            try:
                raw_body = base64.b64decode(raw_body).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                raise ValueError("Request body could not be decoded")
        if "application/x-www-form-urlencoded" in content_type.lower():
            return dict(parse_qsl(raw_body, keep_blank_values=True))
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            raise ValueError("Request body must be valid JSON")
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    return body


def _normalize_path(raw_path, path_params):
    path = raw_path.rstrip("/") or "/"
    return path


def _extract_jwt(headers):
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        return None, None
    payload = decode_access_token(auth[7:])
    return payload.get("user_id"), payload.get("role")
