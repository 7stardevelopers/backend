from pydantic import BaseModel, Field
from typing import Optional


class CreateTicketSchema(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    category: Optional[str] = "OTHER"
    booking_id: Optional[str] = None
    priority: str = "MEDIUM"


class UpdateTicketSchema(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None


class ReplySchema(BaseModel):
    content: str = Field(..., min_length=1)
    is_internal: bool = False
