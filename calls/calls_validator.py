from typing import Optional
from pydantic import BaseModel, Field


class InitiateCallSchema(BaseModel):
    booking_id: Optional[str] = None
    target_user_id: Optional[str] = None   # admin/support direct-call path, no booking required
    target: Optional[str] = Field(None, pattern="^(customer|provider)$")


class CallStatusCallbackSchema(BaseModel):
    CallSid: str
    Status: Optional[str] = None
    DialCallStatus: Optional[str] = None
