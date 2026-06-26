from pydantic import BaseModel, Field
from typing import Optional


class UpdateBookingSchema(BaseModel):
    status: Optional[str] = None
    provider_id: Optional[str] = None
    notes: Optional[str] = None


class CreateServiceAdminSchema(BaseModel):
    category_id: str
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    base_price: int = Field(..., gt=0)
    image_url: Optional[str] = None
    instant_available: bool = False
    sort_order: int = 0
