from pydantic import BaseModel, Field
from typing import Optional


class ConnectSchema(BaseModel):
    booking_id: Optional[str] = None


class SendMessageSchema(BaseModel):
    booking_id: str
    text: str = Field(..., min_length=1)
    message_type: str = "text"


class LocationUpdateSchema(BaseModel):
    booking_id: Optional[str] = None
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
