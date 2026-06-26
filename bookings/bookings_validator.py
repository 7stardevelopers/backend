from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class CreateBookingSchema(BaseModel):
    service_id: str = Field(..., min_length=1)
    scheduled_at: datetime
    address_id: Optional[str] = None
    address_snapshot: Optional[dict] = None
    service_snapshot: Optional[dict] = None
    sub_total: int = Field(..., gt=0)
    discount: int = 0
    total_amount: int = Field(..., gt=0)
    coupon_id: Optional[str] = None
    is_instant: bool = False
    customer_notes: Optional[str] = None
    items: Optional[List[dict]] = None


class UpdateStatusSchema(BaseModel):
    status: str
    booking_id: Optional[str] = None


class VerifyDoorOTPSchema(BaseModel):
    otp: str = Field(..., min_length=4, max_length=4)


class AddTipSchema(BaseModel):
    amount: int = Field(..., gt=0)
