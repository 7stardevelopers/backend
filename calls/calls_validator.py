from typing import Optional, List, Any
from pydantic import BaseModel, ConfigDict, Field


class InitiateCallSchema(BaseModel):
    booking_id: Optional[str] = None
    target_user_id: Optional[str] = None   # admin/support direct-call path, no booking required
    target: Optional[str] = Field(None, pattern="^(customer|provider)$")


class CallStatusCallbackSchema(BaseModel):
    """Exotel Connect status callback (JSON or form-encoded). Unknown fields ignored."""
    model_config = ConfigDict(extra="ignore")

    CallSid: str
    Status: Optional[str] = None             # completed | failed | busy | no-answer | canceled
    DialCallStatus: Optional[str] = None     # sent by some Exotel flows instead of Status
    ConversationDuration: Optional[Any] = None
    StartTime: Optional[str] = None
    EndTime: Optional[str] = None
    RecordingUrl: Optional[str] = None
    Legs: Optional[List[Any]] = None
