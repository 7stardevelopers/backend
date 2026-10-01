from pydantic import BaseModel, Field
from typing import Optional, List


class CreateInstantBookingSchema(BaseModel):
    service_id: str
    address_id: Optional[str] = None
    address_snapshot: Optional[dict] = None
    # Ignored — priced server-side (bookings/booking_pricing.py)
    sub_total: Optional[int] = None
    total_amount: Optional[int] = None
    items: Optional[List[dict]] = None
    customer_notes: Optional[str] = None
