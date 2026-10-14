from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Literal
from datetime import datetime, timezone


class CreateBookingSchema(BaseModel):
    service_id: str = Field(..., min_length=1)
    # INSTANT ("Now"): scheduled_at is ignored and set to the booking time.
    # SCHEDULED: scheduled_at is required (slot rules in bookings/dispatch_rules.py).
    # Older apps send is_instant instead; is_instant=true means INSTANT.
    booking_type: Optional[Literal["SCHEDULED", "INSTANT"]] = None
    scheduled_at: Optional[datetime] = None
    address_id: Optional[str] = None

    @model_validator(mode="after")
    def resolve_type_and_time(self):
        if self.booking_type is None:
            self.booking_type = "INSTANT" if self.is_instant else "SCHEDULED"
        self.is_instant = self.booking_type == "INSTANT"
        if self.is_instant:
            self.scheduled_at = None
            return self
        if self.scheduled_at is None:
            raise ValueError("scheduled_at is required for a scheduled booking")
        # Stored as naive UTC — the DB session runs in UTC, so a "+05:30" value
        # must be converted, not just have its tzinfo dropped by the driver.
        v = self.scheduled_at
        aware = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if aware <= datetime.now(timezone.utc):
            raise ValueError("scheduled_at must be in the future")
        self.scheduled_at = aware.astimezone(timezone.utc).replace(tzinfo=None)
        return self

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
    # PAY_NOW: online at booking, workers see the job once it's paid.
    # PAY_AFTER: cash or online once the work is done, workers see it at once.
    payment_mode: Literal["PAY_NOW", "PAY_AFTER"] = "PAY_AFTER"


class UpdateStatusSchema(BaseModel):
    status: str
    booking_id: Optional[str] = None


class VerifyDoorOTPSchema(BaseModel):
    otp: str = Field(..., min_length=4, max_length=4)


class AddTipSchema(BaseModel):
    amount: int = Field(..., gt=0, le=1_000_000)
