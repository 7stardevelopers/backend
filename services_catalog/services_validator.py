from pydantic import BaseModel, Field
from typing import Optional


class CreateServiceSchema(BaseModel):
    category_id: str
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    base_price: int = Field(..., gt=0)
    image_url: Optional[str] = None
    instant_available: bool = False
    sort_order: int = 0


class UpdateServiceSchema(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    base_price: Optional[int] = None
    image_url: Optional[str] = None
    instant_available: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
