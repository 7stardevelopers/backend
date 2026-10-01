"""Exotel Connect API (masked calling).

Exotel rings `From` first; once answered it dials `To` and bridges the two.
Both parties only ever see the ExoPhone (`CallerId`), never each other's number.

Security: credentials are sent with HTTP basic auth (`auth=`), never embedded in
the URL — a URL like https://key:token@host/... ends up in exception messages
and CloudWatch tracebacks.
"""
import os
import re

import requests

REQUIRED_ENV = ("EXOTEL_SID", "EXOTEL_API_KEY", "EXOTEL_API_TOKEN", "EXOTEL_SUBDOMAIN", "EXOPHONE")
TIMEOUT_SEC = 10
DEFAULT_TIME_LIMIT_SEC = 1800   # 30 min hard cap per call (cost control)


class ExotelError(Exception):
    """Raised with a message that is safe to log (no credentials)."""


class ExotelNotConfigured(ExotelError):
    pass


def is_configured() -> bool:
    return all(os.environ.get(k) for k in REQUIRED_ENV)


def normalize_number(phone) -> str:
    """Return Indian numbers as 0 + 10 digits (Exotel's preferred format)."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Invalid phone number")
    return "0" + digits


def connect_call(from_number: str, to_number: str) -> str:
    """Start a bridged call. Returns Exotel's CallSid. Raises ExotelError."""
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise ExotelNotConfigured(f"Exotel not configured (missing: {', '.join(missing)})")

    sid = os.environ["EXOTEL_SID"]
    subdomain = os.environ["EXOTEL_SUBDOMAIN"].strip().removeprefix("https://").strip("/")
    url = f"https://{subdomain}/v1/Accounts/{sid}/Calls/connect.json"

    payload = {
        "From": normalize_number(from_number),
        "To": normalize_number(to_number),
        "CallerId": os.environ["EXOPHONE"].strip(),
        "TimeLimit": str(_int_env("EXOTEL_TIME_LIMIT_SEC", DEFAULT_TIME_LIMIT_SEC)),
        "Record": "true" if os.environ.get("EXOTEL_RECORD", "false").lower() == "true" else "false",
    }
    callback_url = os.environ.get("EXOTEL_STATUS_CALLBACK_URL", "")
    if callback_url:
        # JSON callback with only the final status — parse_request also accepts
        # form-encoded bodies, but JSON keeps Legs[] structured.
        payload["StatusCallback"] = callback_url
        payload["StatusCallbackContentType"] = "application/json"
        payload["StatusCallbackEvents[0]"] = "terminal"

    try:
        resp = requests.post(
            url,
            data=payload,
            auth=(os.environ["EXOTEL_API_KEY"], os.environ["EXOTEL_API_TOKEN"]),
            timeout=TIMEOUT_SEC,
        )
    except requests.Timeout:
        raise ExotelError("Exotel request timed out")
    except requests.RequestException as e:
        # str(e) can include the URL — report only the exception type
        raise ExotelError(f"Exotel request failed ({type(e).__name__})")

    body = _json_or_none(resp)
    if resp.status_code >= 400:
        raise ExotelError(f"Exotel HTTP {resp.status_code}: {_exotel_message(body)}")

    call_sid = ((body or {}).get("Call") or {}).get("Sid")
    if not call_sid:
        raise ExotelError("Exotel response had no Call.Sid")
    return call_sid


def _json_or_none(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def _exotel_message(body) -> str:
    """Exotel errors look like {"RestException": {"Status": 400, "Message": "..."}}."""
    if isinstance(body, dict):
        exc = body.get("RestException") or {}
        if exc.get("Message"):
            return str(exc["Message"])[:200]
    return "unknown error"


def _int_env(key, default):
    try:
        return max(1, int(os.environ.get(key, default)))
    except (TypeError, ValueError):
        return default
