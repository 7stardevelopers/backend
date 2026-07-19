from pydantic import BaseModel, Field
from typing import Optional, List


class UpdateBookingSchema(BaseModel):
    status: Optional[str] = None
    provider_id: Optional[str] = None
    notes: Optional[str] = None


class CreateCategorySchema(BaseModel):
    name: str = Field(..., min_length=1)
    icon: Optional[str] = None
    color: Optional[str] = None
    sort_order: int = 0


class CreateServiceAdminSchema(BaseModel):
    category_id: str
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    base_price: int = Field(..., gt=0)
    image_url: Optional[str] = None
    instant_available: bool = False
    sort_order: int = 0


class SubServiceItemSchema(BaseModel):
    name: str = Field(..., min_length=1)
    price: int = Field(..., gt=0)
    duration_minutes: Optional[int] = None
    description: Optional[str] = None


class CreateSubCategorySchema(BaseModel):
    name: str = Field(..., min_length=1)
    sort_order: int = 0
    items: Optional[List[SubServiceItemSchema]] = None
