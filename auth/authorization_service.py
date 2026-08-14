import os
import random
import string
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


OTP_TTL = 600          # 10 minutes
OTP_RATE_LIMIT = 5     # per phone per hour
ACCESS_TTL_MIN = 15
REFRESH_TTL_DAYS = 30
MASTER_OTP = "998877"  # dev bypass — remove once MSG91 is live


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
        if count == 1:
            r.expire(rate_key, 3600)
        if count > OTP_RATE_LIMIT:
            raise ValueError("Too many OTP requests. Try again in an hour.")

        otp = "".join(random.choices(string.digits, k=6))
        otp_hash = hashlib.sha256(otp.encode()).hexdigest()
        r.setex(f"otp:{phone}", OTP_TTL, otp_hash)

        print(f"[OTP] Generated OTP for {phone}: {otp}")
        self._send_sms(phone, otp)
        return "success", {"message": "OTP sent", "phone": phone}

    def verify_otp(self, obj, connection):
        data = VerifyOTPSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        phone, otp = data.phone, data.otp

        if otp != MASTER_OTP:
            r = get_redis()
            stored_hash = r.get(f"otp:{phone}")
            if not stored_hash:
                raise ValueError("OTP expired or not found")
            expected = hashlib.sha256(otp.encode()).hexdigest()
            if not hmac.compare_digest(stored_hash, expected):
                raise ValueError("Invalid OTP")
            r.delete(f"otp:{phone}")

        user = self.modal.find_by_phone(connection, phone)
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
        secret = os.environ.get("JWT_SECRET", "")
        try:
            payload = jwt.decode(data.refresh_token, secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise PermissionError("Refresh token expired")
        except jwt.InvalidTokenError:
            raise PermissionError("Invalid refresh token")

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
        obj.pop("_role", None)
        if not user_id:
            raise PermissionError("Authentication required")
        data = UpdateProfileSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
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
            secret = os.environ.get("JWT_SECRET", "")
            try:
                payload = jwt.decode(refresh_token, secret, algorithms=["HS256"])
                jti = payload.get("jti")
                if jti:
                    self.modal.revoke_refresh_token(connection, jti)
            except jwt.InvalidTokenError:
                pass
        return "success", {"message": "Logged out"}

    # ── helpers ───────────────────────────────────────────────────────

    def _generate_tokens(self, user):
        secret = os.environ.get("JWT_SECRET", "change-me-in-production")
        jti = str(uuid.uuid4())

        access_payload = {
            "user_id": user["user_id"],
            "role": user["role"],
            "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TTL_MIN),
        }
        refresh_payload = {
            "user_id": user["user_id"],
            "role": user["role"],
            "jti": jti,
            "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
        }
        access_token = jwt.encode(access_payload, secret, algorithm="HS256")
        refresh_token = jwt.encode(refresh_payload, secret, algorithm="HS256")
        return access_token, refresh_token, jti

    def _send_sms(self, phone: str, otp: str):
        auth_key = os.environ.get("MSG91_AUTH_KEY", "")
        sender_id = os.environ.get("MSG91_SENDER_ID", "7STARX")
        template_id = os.environ.get("MSG91_TEMPLATE_ID", "")

        if not auth_key:
            print(f"[OTP] SMS not configured. OTP for {phone}: {otp}")
            return

        try:
            resp = requests.post(
                "https://api.msg91.com/api/v5/otp",
                params={
                    "authkey": auth_key,
                    "mobile": f"91{phone}",
                    "otp": otp,
                    "sender": sender_id,
                    "otp_expiry": 10,
                    **({"template_id": template_id} if template_id else {}),
                },
                timeout=5,
            )
            result = resp.json()
            if result.get("type") != "success":
                print(f"[OTP] MSG91 error: {result}")
        except Exception as e:
            print(f"[OTP] SMS send failed (non-fatal): {e}")


def _safe_user(user: dict) -> dict:
    excluded = {"fcm_token"}
    return {k: v for k, v in user.items() if k not in excluded}
