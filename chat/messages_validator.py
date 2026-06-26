from pydantic import BaseModel, Field
from typing import Optional


class SendMessageSchema(BaseModel):
    booking_id: str
    text: str = Field(..., min_length=1, max_length=2000)
    message_type: str = "text"
