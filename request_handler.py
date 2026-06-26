import json
import os
import jwt
from utilities.privacy import mask_phone


def parse_request(event):
    method = event.get("httpMethod", "GET").upper()

    raw_path = event.get("path", "/")
    path = _normalize_path(raw_path, event.get("pathParameters") or {})

    raw_body = event.get("body") or "{}"
    try:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    except (json.JSONDecodeError, TypeError):
        body = {}

    query = event.get("queryStringParameters") or {}
    body.update({k: v for k, v in query.items() if k not in body})

    user_id, role = _extract_jwt(event.get("headers") or {})

    return {"method": method, "path": path, "body": body, "user_id": user_id, "role": role}


def _normalize_path(raw_path, path_params):
    path = raw_path.rstrip("/") or "/"
    return path


def _extract_jwt(headers):
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        return None, None
    token = auth[7:]
    try:
        secret = os.environ.get("JWT_SECRET", "")
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload.get("user_id"), payload.get("role")
    except jwt.ExpiredSignatureError:
        raise PermissionError("Token expired")
    except jwt.InvalidTokenError:
        raise PermissionError("Invalid token")
