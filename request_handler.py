import json
from utilities.auth_tokens import decode_access_token


def parse_request(event):
    method = event.get("httpMethod", "GET").upper()

    raw_path = event.get("path", "/")
    path = _normalize_path(raw_path, event.get("pathParameters") or {})

    raw_body = event.get("body") or "{}"
    try:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    except (json.JSONDecodeError, TypeError):
        raise ValueError("Request body must be valid JSON")
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")

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
    payload = decode_access_token(auth[7:])
    return payload.get("user_id"), payload.get("role")
