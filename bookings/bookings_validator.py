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
        # Stored as naive UTC — the DB session runs in UTC, so a "+05:30" value
        # must be converted, not just have its tzinfo dropped by the driver.
        aware = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if aware <= datetime.now(timezone.utc):
            raise ValueError("scheduled_at must be in the future")
        return aware.astimezone(timezone.utc).replace(tzinfo=None)
    address_snapshot: Optional[dict] = None
    service_snapshot: Optional[dict] = None
    # Accepted for backwards compatibility but IGNORED — prices are computed
    # server-side in bookings/booking_pricing.py.
    sub_total: Optional[int] = None
    discount: Optional[int] = None
    total_amount: Optional[int] = None
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
    amount: int = Field(..., gt=0, le=1_000_000)
