from pydantic import BaseModel, Field
from typing import Literal, Optional

MAX_MESSAGE_LENGTH = 1000


class SendMessageSchema(BaseModel):
    booking_id: str
    text: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)
    message_type: Literal["text"] = "text"
    # Client-generated id echoed back on the sender's copy so the app can
    # reconcile its optimistic bubble. Not stored (Phase 2 adds dedupe).
    client_id: Optional[str] = Field(None, max_length=64)
