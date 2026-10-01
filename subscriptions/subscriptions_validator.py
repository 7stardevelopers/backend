from typing import Optional
from pydantic import BaseModel, Field


class SubscribeSchema(BaseModel):
    plan_id: str
    payment_id: Optional[str] = None


class CreatePlanSchema(BaseModel):
    name: str
    price: int
    bookings_included: Optional[int] = None
    discount_pct: int = Field(..., ge=0, le=100)
    features: Optional[dict] = None
    sort_order: int = 0
    is_active: bool = True


class UpdatePlanSchema(BaseModel):
    name: Optional[str] = None
    price: Optional[int] = None
    bookings_included: Optional[int] = None
    discount_pct: Optional[int] = Field(None, ge=0, le=100)
    features: Optional[dict] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
