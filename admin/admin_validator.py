from pydantic import BaseModel, Field
from typing import Optional, List


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


class SubServiceItemSchema(BaseModel):
    name: str = Field(..., min_length=1)
    price: int = Field(..., gt=0)
    duration_minutes: Optional[int] = None
    description: Optional[str] = None


class CreateSubCategorySchema(BaseModel):
    name: str = Field(..., min_length=1)
    sort_order: int = 0
    items: Optional[List[SubServiceItemSchema]] = None


class CreateCategorySchema(BaseModel):
    name:        str = Field(..., min_length=1)
    icon:        Optional[str] = None
    color:       Optional[str] = None
    description: Optional[str] = None
    sort_order:  int = 0
    is_active:   bool = True


class UpdateCategorySchema(BaseModel):
    name:        Optional[str] = None
    icon:        Optional[str] = None
    color:       Optional[str] = None
    description: Optional[str] = None
    sort_order:  Optional[int] = None
    is_active:   Optional[bool] = None


class CreateSubscriptionPlanSchema(BaseModel):
    name:              str = Field(..., min_length=1)
    price:             int = Field(..., gt=0)
    bookings_included: Optional[int] = Field(None, gt=0)
    discount_pct:      int = Field(0, ge=0, le=100)
    description:       Optional[str] = None
    is_active:         bool = True
    sort_order:        int = 0


class UpdateSubscriptionPlanSchema(BaseModel):
    name:              Optional[str] = None
    price:             Optional[int] = Field(None, gt=0)
    bookings_included: Optional[int] = Field(None, gt=0)
    discount_pct:      Optional[int] = Field(None, ge=0, le=100)
    description:       Optional[str] = None
    is_active:         Optional[bool] = None
    sort_order:        Optional[int] = None


class SendAnnouncementSchema(BaseModel):
    title:       str = Field(..., min_length=1)
    body:        str = Field(..., min_length=1)
    target_role: str = "ALL"
