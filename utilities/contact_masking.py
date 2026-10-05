"""Mask contact details typed into chat so users can't bypass masked calling.

Masks Indian mobile numbers (with optional +91 / 0 and spaces or dashes),
email addresses and UPI IDs. Returns (masked_text, was_masked)."""
import re

# 10-digit Indian mobile starting 6-9, optionally prefixed by +91 / 91 / 0,
# digits may be separated by single spaces, dots or dashes.
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s.-]?|0)?[6-9](?:[\s.-]?\d){9}(?!\d)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# UPI: name@handle with no dot in the handle (okicici, ybl, paytm, upi, ...)
_UPI = re.compile(r"\b[A-Za-z0-9._-]{2,}@[A-Za-z]{2,}\b")


def _mask_phone(match) -> str:
    digits = re.sub(r"\D", "", match.group(0))[-10:]
    return f"{digits[:2]}xxxxxx{digits[-2:]}"


def mask_contact_info(text: str):
    if not text:
        return text, False
    masked = _EMAIL.sub("[email hidden]", text)
    masked = _UPI.sub("[UPI hidden]", masked)
    masked = _PHONE.sub(_mask_phone, masked)
    return masked, masked != text
