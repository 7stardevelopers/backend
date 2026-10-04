import os
import jwt

MIN_SECRET_LENGTH = 32


def is_deployed() -> bool:
    """True when running in a real AWS environment (staging or production)."""
    return os.environ.get("ENVIRONMENT", "").lower() in ("staging", "production")


def is_production() -> bool:
    return os.environ.get("ENVIRONMENT", "").lower() == "production"


def get_jwt_secret() -> str:
    """Return JWT_SECRET, failing closed. Never fall back to a default key —
    an empty or guessable secret lets anyone forge ADMIN tokens."""
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set")
    if is_deployed() and len(secret) < MIN_SECRET_LENGTH and not _warned_short:
        _warn_short_secret()
    return secret


_warned_short = False


def _warn_short_secret():
    global _warned_short
    _warned_short = True
    print(f"[Auth] WARNING: JWT_SECRET is shorter than {MIN_SECRET_LENGTH} characters — rotate it")


def decode_access_token(token: str) -> dict:
    """Decode a token for API / WebSocket access. Refresh tokens are rejected.
    Raises PermissionError("Token expired" | "Invalid token")."""
    payload = _decode(token, "Token expired", "Invalid token")
    if not _is_access(payload):
        raise PermissionError("Invalid token")
    return payload


def decode_refresh_token(token: str) -> dict:
    payload = _decode(token, "Refresh token expired", "Invalid refresh token")
    if not _is_refresh(payload):
        raise PermissionError("Invalid refresh token")
    return payload


def _decode(token, expired_msg, invalid_msg):
    try:
        return jwt.decode(token, get_jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise PermissionError(expired_msg)
    except jwt.InvalidTokenError:
        raise PermissionError(invalid_msg)


# Tokens issued before "typ" was added: refresh tokens carry a jti, access tokens don't.
def _is_access(payload):
    typ = payload.get("typ")
    return typ == "access" if typ else "jti" not in payload


def _is_refresh(payload):
    typ = payload.get("typ")
    return typ == "refresh" if typ else "jti" in payload


# Share-tracking links: a read-only, booking-scoped token. typ "track" is never
# accepted by decode_access_token, so a leaked link can't call the API.
TRACK_TOKEN_HOURS = 4


def issue_track_token(booking_id: str, hours: int = TRACK_TOKEN_HOURS) -> str:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    payload = {"typ": "track", "bid": str(booking_id), "iat": now, "exp": now + timedelta(hours=hours)}
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def decode_track_token(token: str) -> str:
    """Returns the booking id. Raises PermissionError("Link expired" | "Invalid link")."""
    payload = _decode(token, "Link expired", "Invalid link")
    if payload.get("typ") != "track" or not payload.get("bid"):
        raise PermissionError("Invalid link")
    return payload["bid"]
