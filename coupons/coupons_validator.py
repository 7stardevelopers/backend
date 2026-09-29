from pydantic import BaseModel, Field
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
    min_order_amount: int = 0
    max_discount: Optional[int] = None
    max_uses: int = 1000
    service_ids: Optional[List[str]] = None
    expires_at: datetime
    source: str = "MANUAL"
    color: Optional[str] = None


class UpdateCouponSchema(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[str] = Field(None, pattern="^(FLAT|PERCENT|GPAY)$")
    value: Optional[int] = Field(None, gt=0)
    min_order_amount: Optional[int] = None
    max_discount: Optional[int] = None
    max_uses: Optional[int] = None
    service_ids: Optional[List[str]] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None
    color: Optional[str] = None
