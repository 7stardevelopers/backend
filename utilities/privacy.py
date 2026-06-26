import re


def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6:
        return phone
    return phone[:3] + "****" + phone[-3:]


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email
    parts = email.split("@")
    local = parts[0]
    if len(local) <= 2:
        return email
    return local[:2] + "***@" + parts[1]


def redact_pii(data: dict) -> dict:
    sensitive = {"phone", "email", "aadhaar", "pan", "bank_account_number"}
    result = {}
    for k, v in data.items():
        if k in sensitive and isinstance(v, str):
            result[k] = "[REDACTED]"
        else:
            result[k] = v
    return result
