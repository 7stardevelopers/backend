from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime, timezone


class CreateBookingSchema(BaseModel):
    service_id: str = Field(..., min_length=1)
    scheduled_at: datetime
    address_id: Optional[str] = None

    @field_validator("scheduled_at")
    @classmethod
    def must_be_future(cls, v):
        now = datetime.now(timezone.utc)
        aware = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if aware <= now:
            raise ValueError("scheduled_at must be in the future")
        return v
    address_snapshot: Optional[dict] = None
    service_snapshot: Optional[dict] = None
    sub_total: int = Field(..., gt=0)
    discount: int = 0
    total_amount: int = Field(..., ge=0)  # can be 0 when coins/coupon fully cover the sub_total
    coupon_id: Optional[str] = None
    is_instant: bool = False
    customer_notes: Optional[str] = None
    items: Optional[List[dict]] = None
    requested_provider_id: Optional[str] = None
    coins_used: int = Field(0, ge=0)


class UpdateStatusSchema(BaseModel):
    status: str
    booking_id: Optional[str] = None


class VerifyDoorOTPSchema(BaseModel):
    otp: str = Field(..., min_length=4, max_length=4)


class AddTipSchema(BaseModel):
    amount: int = Field(..., gt=0)
