from pydantic import BaseModel, Field
from typing import Optional


class CreateInstantBookingSchema(BaseModel):
    service_id: str
    address_id: Optional[str] = None
    address_snapshot: Optional[dict] = None
    sub_total: int = Field(..., gt=0)
    total_amount: int = Field(..., gt=0)
    customer_notes: Optional[str] = None
