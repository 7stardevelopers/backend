import json
import re
from utilities.privacy import mask_phone

# One JSON line per API call so CloudWatch Logs Insights can filter on fields:
#   fields @timestamp, method, path, status, duration_ms, error
#   | filter type = "api" and status >= 400 | sort @timestamp desc

_SECRET_KEY = re.compile(r"otp|token|password|secret|authorization|aadhaar|account_number|^pan$|^pan_number$", re.I)
_MAX_STRING = 300
_MAX_BODY = 8000


def redact(value, key=""):
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (str, int, float)):
        if _SECRET_KEY.search(key):
            return "[REDACTED]"
        if "phone" in key.lower():
            return mask_phone(str(value))
    if isinstance(value, str) and len(value) > _MAX_STRING:
        return f"{value[:60]}...({len(value)} chars)"
    return value


def _cap(value):
    safe = redact(value)
    text = json.dumps(safe, default=str)
    if len(text) <= _MAX_BODY:
        return safe
    return {"_truncated": True, "preview": text[:_MAX_BODY]}


def log_api(method, path, status, duration_ms, user_id=None, role=None, request_id=None,
            req_body=None, resp_body=None, error=None, trace=None):
    level = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
    entry = {
        "type": "api",
        "level": level,
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": duration_ms,
        "user_id": user_id,
        "role": role,
        "request_id": request_id,
        "request": _cap(req_body),
        "response": _cap(resp_body),
    }
    if error:
        entry["error"] = error
    if trace:
        entry["traceback"] = trace
    try:
        print(json.dumps(entry, default=str))
    except Exception as e:  # logging must never break a request
        print(f"[LOG ERROR] {method} {path} {status}: {e}")
