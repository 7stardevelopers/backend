from pydantic import BaseModel, Field, field_validator
from typing import Optional
import re


class SendOTPSchema(BaseModel):
    phone: str = Field(..., min_length=10, max_length=13)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        cleaned = re.sub(r"\D", "", v)
        if len(cleaned) not in (10, 12):
            raise ValueError("Phone must be 10 digits (or 12 with country code)")
        return cleaned[-10:]


class VerifyOTPSchema(BaseModel):
    phone: str = Field(..., min_length=10, max_length=13)
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        cleaned = re.sub(r"\D", "", v)
        return cleaned[-10:]


class RefreshTokenSchema(BaseModel):
    refresh_token: str


class UpdateProfileSchema(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    photo_url: Optional[str] = None


class AddAddressSchema(BaseModel):
    label: Optional[str] = Field(None, max_length=50)
    full_address: str = Field(..., min_length=5)
    lat: Optional[float] = None
    lng: Optional[float] = None
    pincode: Optional[str] = Field(None, max_length=10)
    city: Optional[str] = Field(None, max_length=100)
    is_default: bool = False
