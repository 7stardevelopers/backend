from pydantic import BaseModel, Field


class RedeemReferralSchema(BaseModel):
    code: str = Field(..., min_length=4, max_length=12)
