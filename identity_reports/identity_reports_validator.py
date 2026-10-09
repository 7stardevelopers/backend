from pydantic import BaseModel, Field
from typing import Literal, Optional


class DoorCheckSchema(BaseModel):
    # True: "Yes, it's them" → door OTP is issued. False: "No, different person" → admin report.
    match: bool
    note: Optional[str] = Field(None, max_length=500)


class ResolveReportSchema(BaseModel):
    status: Literal["OPEN", "ACTION_TAKEN", "DISMISSED"]
    admin_note: Optional[str] = Field(None, max_length=2000)
