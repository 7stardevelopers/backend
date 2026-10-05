import os
import secrets
import hashlib
import hmac
import uuid
import jwt
import requests
from datetime import datetime, timedelta, timezone

from auth.authorization_modal import UsersMaster
from auth.authorization_validator import (
    SendOTPSchema, VerifyOTPSchema, RefreshTokenSchema,
    UpdateProfileSchema, AddAddressSchema, UpdateAddressSchema,
)
from utilities.redis_connection import get_redis
from utilities.auth_tokens import (
    get_jwt_secret, decode_refresh_token, is_deployed, is_production,
)


OTP_TTL = 600          # 10 minutes
OTP_RATE_LIMIT = 5     # per phone per hour
OTP_MAX_ATTEMPTS = 5   # wrong guesses before the OTP is invalidated
ACCESS_TTL_MIN = 15
REFRESH_TTL_DAYS = 30
MASTER_OTP = "998877"  # testing bypass — on by default outside production, never in production


def _otp_bypass_enabled():
    """Master OTP works in local dev and staging without any config (set
    OTP_BYPASS_ENABLED=false to turn it off there). Always off in production."""
    if is_production():
        return False
    return os.environ.get("OTP_BYPASS_ENABLED", "true").lower() != "false"


class AuthorizationService:
    def __init__(self):
        self.modal = UsersMaster()

    # ── OTP ───────────────────────────────────────────────────────────

    def send_otp(self, obj, connection):
        data = SendOTPSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        phone = data.phone
        r = get_redis()

        rate_key = f"otp_rate:{phone}"
        count = r.incr(rate_key)
        if r.ttl(rate_key) < 0:
            r.expire(rate_key, 3600)
        if count > OTP_RATE_LIMIT:
            raise ValueError("Too many OTP requests. Try again in an hour.")

        otp = f"{secrets.randbelow(10**6):06d}"
        otp_hash = hashlib.sha256(otp.encode()).hexdigest()
        r.setex(f"otp:{phone}", OTP_TTL, otp_hash)
        r.delete(f"otp_attempts:{phone}")

        status, detail = self._send_sms(phone, otp)
        if status != "ok":
            print(f"[OTP] SMS not dispatched for {phone}: {detail}")
        return "success", {
            "message": "OTP sent",
            "phone": phone,
            "sms_dispatched": status == "ok",
        }

    def verify_otp(self, obj, connection):
        data = VerifyOTPSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        phone, otp = data.phone, data.otp

        if _otp_bypass_enabled() and otp == MASTER_OTP:
            pass
        else:
            r = get_redis()
            otp_key, attempts_key = f"otp:{phone}", f"otp_attempts:{phone}"
            stored_hash = r.get(otp_key)
            if not stored_hash:
                raise ValueError("OTP expired or not found")
            expected = hashlib.sha256(otp.encode()).hexdigest()
            if not hmac.compare_digest(stored_hash, expected):
                attempts = r.incr(attempts_key)
                if r.ttl(attempts_key) < 0:
                    r.expire(attempts_key, OTP_TTL)
                if attempts >= OTP_MAX_ATTEMPTS:
                    r.delete(otp_key)
                    r.delete(attempts_key)
                    raise ValueError("Too many wrong attempts. Request a new OTP.")
                raise ValueError("Invalid OTP")
            r.delete(otp_key)
            r.delete(attempts_key)

        user = self.modal.find_by_phone(connection, phone)
        if user and user.get("status") != "ACTIVE":
            raise PermissionError("Account inactive")
        is_new = user is None
        if is_new:
            user = self.modal.create(connection, phone, role=data.role)
        elif data.role == "PROVIDER" and user.get("role") == "CUSTOMER":
            self.modal.update(connection, user["user_id"], {"role": "PROVIDER"})
            user = self.modal.find_by_id(connection, user["user_id"])

        access_token, refresh_token, jti = self._generate_tokens(user)
        self.modal.store_refresh_token(
            connection,
            user["user_id"],
            jti,
            datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
        )

        return "success", {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": _safe_user(user),
            "is_new_user": is_new,
        }

    def refresh_token(self, obj, connection):
        raw = {k: v for k, v in obj.items() if not k.startswith("_")}
        data = RefreshTokenSchema(**raw)
        payload = decode_refresh_token(data.refresh_token)

        jti = payload.get("jti")
        record = self.modal.find_refresh_token(connection, jti)
        if not record or record.get("revoked"):
            raise PermissionError("Refresh token revoked")

        user = self.modal.find_by_id(connection, payload["user_id"])
        if not user or user["status"] != "ACTIVE":
            raise PermissionError("Account inactive")

        access_token, new_refresh, new_jti = self._generate_tokens(user)
        self.modal.revoke_refresh_token(connection, jti)
        self.modal.store_refresh_token(
            connection,
            user["user_id"],
            new_jti,
            datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
        )
        return "success", {"access_token": access_token, "refresh_token": new_refresh}

    def get_profile(self, obj, connection):
        user_id = obj.get("_user_id")
        if not user_id:
            raise PermissionError("Authentication required")
        user = self.modal.find_by_id(connection, user_id)
        if not user:
            raise ValueError("User not found")
        addresses = self.modal.get_addresses(connection, user_id)
        return "success", {**_safe_user(user), "addresses": addresses}

    def update_profile(self, obj, connection):
        user_id = obj.pop("_user_id", None)
        role = obj.pop("_role", None)
        if not user_id:
            raise PermissionError("Authentication required")
        data = UpdateProfileSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        # A worker's photo is their registration selfie: set once via POST
        # /providers/me/photo, changed only through an admin reset.
        if role == "PROVIDER" and "photo_url" in fields:
            from providers.providers_service import PHOTO_LOCKED_MSG
            raise PermissionError(PHOTO_LOCKED_MSG)
        if fields:
            self.modal.update(connection, user_id, fields)
        return "success", {"message": "Profile updated"}

    def add_address(self, obj, connection):
        user_id = obj.pop("_user_id", None)
        obj.pop("_role", None)
        if not user_id:
            raise PermissionError("Authentication required")
        data = AddAddressSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        address_id = self.modal.add_address(connection, user_id, data.model_dump(exclude_none=True))
        return "created", {"address_id": address_id}

    def update_address(self, obj, connection):
        user_id = obj.pop("_user_id", None)
        obj.pop("_role", None)
        address_id = obj.pop("id", None)
        if not user_id:
            raise PermissionError("Authentication required")
        if not address_id:
            raise ValueError("address_id required")
        data = UpdateAddressSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        fields = data.model_dump(exclude_none=True)
        if not fields:
            return "success", {"message": "Address updated"}
        updated = self.modal.update_address(connection, user_id, address_id, fields)
        if not updated:
            raise ValueError("Address not found")
        return "success", {"message": "Address updated"}

    def delete_address(self, obj, connection):
        user_id = obj.pop("_user_id", None)
        obj.pop("_role", None)
        address_id = obj.get("id") or obj.get("address_id")
        if not user_id:
            raise PermissionError("Authentication required")
        if not address_id:
            raise ValueError("address_id required")
        self.modal.delete_address(connection, user_id, address_id)
        return "success", {"message": "Address deleted"}

    def logout(self, obj, connection):
        refresh_token = obj.get("refresh_token")
        if refresh_token:
            try:
                payload = decode_refresh_token(refresh_token)
                self.modal.revoke_refresh_token(connection, payload["jti"])
            except (PermissionError, KeyError):
                pass
        return "success", {"message": "Logged out"}

    # ── helpers ───────────────────────────────────────────────────────

    def _generate_tokens(self, user):
        secret = get_jwt_secret()
        jti = str(uuid.uuid4())

        access_payload = {
            "user_id": user["user_id"],
            "role": user["role"],
            "typ": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TTL_MIN),
        }
        refresh_payload = {
            "user_id": user["user_id"],
            "role": user["role"],
            "jti": jti,
            "typ": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
        }
        access_token = jwt.encode(access_payload, secret, algorithm="HS256")
        refresh_token = jwt.encode(refresh_payload, secret, algorithm="HS256")
        return access_token, refresh_token, jti

    def _send_sms(self, phone: str, otp: str):
        """Send the OTP over SMS via the MSG91 v5 Flow API. Never raises —
        returns ("ok", None) on success or ("error", <detail>) so the caller can
        tell the client that delivery failed without exposing the OTP.

        The Flow API is used (not /api/v5/otp) because the DLT-approved template
        fills a variable named ##num## — the OTP endpoint only injects a
        placeholder literally named ##OTP##. MSG91_OTP_VAR overrides the key if
        the verified template uses a different variable name."""
        auth_key = os.environ.get("MSG91_AUTH_KEY", "")
        template_id = os.environ.get("MSG91_TEMPLATE_ID", "")
        sender = os.environ.get("MSG91_SENDER_ID", "")
        otp_var = os.environ.get("MSG91_OTP_VAR", "num")

        if not auth_key or not template_id:
            missing = ", ".join(
                name for name, val in (
                    ("MSG91_AUTH_KEY", auth_key),
                    ("MSG91_TEMPLATE_ID", template_id),
                ) if not val
            )
            detail = f"MSG91 not fully configured (missing: {missing})"
            if is_deployed():
                print(f"[OTP] {detail}")
            else:
                print(f"[OTP] {detail}. Local dev OTP for {phone}: {otp}")
            return "error", detail

        body = {
            "template_id": template_id,
            "short_url": "0",
            "recipients": [{"mobiles": f"91{phone}", otp_var: otp}],
        }
        if sender:
            body["sender"] = sender

        try:
            resp = requests.post(
                "https://control.msg91.com/api/v5/flow/",
                headers={
                    "authkey": auth_key,
                    "Content-Type": "application/json",
                    "accept": "application/json",
                },
                json=body,
                timeout=5,
            )
            result = resp.json()
            if result.get("type") != "success":
                print(f"[OTP] MSG91 error: {result}")
                return "error", result.get("message") or str(result)
            print(f"[OTP] MSG91 ok for {phone}")
            return "ok", None
        except Exception as e:
            print(f"[OTP] SMS send failed (non-fatal): {e}")
            return "error", str(e)


def _safe_user(user: dict) -> dict:
    excluded = {"fcm_token"}
    return {k: v for k, v in user.items() if k not in excluded}
