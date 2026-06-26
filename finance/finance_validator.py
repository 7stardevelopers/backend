from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ApprovePayoutSchema(BaseModel):
    status: str = Field(..., pattern="^(APPROVED|REJECTED|PROCESSED)$")
    notes: Optional[str] = None


class ReportSchema(BaseModel):
    from_date: Optional[str] = None
    to_date: Optional[str] = None
