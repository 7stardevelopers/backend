from pydantic import BaseModel, Field
from typing import Literal, Optional


Priority = Literal["LOW", "MEDIUM", "HIGH", "URGENT"]


class CreateTicketSchema(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    category: Optional[str] = "OTHER"
    booking_id: Optional[str] = None
    priority: Priority = "MEDIUM"


class UpdateTicketSchema(BaseModel):
    status: Optional[Literal["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]] = None
    priority: Optional[Priority] = None
    assigned_to: Optional[str] = None


class ReplySchema(BaseModel):
    content: str = Field(..., min_length=1)
    is_internal: bool = False
