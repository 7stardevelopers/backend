from pydantic import BaseModel, Field, model_validator
from typing import Optional, List
from datetime import datetime


class ValidateCouponSchema(BaseModel):
    coupon_code: str
    cart_total: int = Field(..., gt=0)
    service_id: Optional[str] = None


class CreateCouponSchema(BaseModel):
    code: str = Field(..., min_length=3, max_length=30)
    title: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., pattern="^(FLAT|PERCENT|GPAY)$")
    value: int = Field(..., gt=0)
    min_order_amount: int = Field(0, ge=0)
    max_discount: Optional[int] = Field(None, gt=0)
    max_uses: int = Field(1000, gt=0)
    service_ids: Optional[List[str]] = None
    expires_at: datetime
    source: str = "MANUAL"
    color: Optional[str] = None

    @model_validator(mode="after")
    def percent_at_most_100(self):
        if self.type in ("PERCENT", "GPAY") and self.value > 100:
            raise ValueError("Percentage coupons cannot exceed 100")
        return self


class UpdateCouponSchema(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[str] = Field(None, pattern="^(FLAT|PERCENT|GPAY)$")
    value: Optional[int] = Field(None, gt=0)
    min_order_amount: Optional[int] = Field(None, ge=0)
    max_discount: Optional[int] = Field(None, gt=0)
    max_uses: Optional[int] = Field(None, gt=0)
    service_ids: Optional[List[str]] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None
    color: Optional[str] = None
