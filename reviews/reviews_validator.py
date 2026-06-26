from pydantic import BaseModel, Field
from typing import Optional


class CreateReviewSchema(BaseModel):
    booking_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None
