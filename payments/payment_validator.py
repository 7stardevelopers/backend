from pydantic import BaseModel, Field
from typing import Optional


class CreateOrderSchema(BaseModel):
    booking_id: str
    amount: int = Field(..., gt=0)
    currency: str = "INR"


class VerifyPaymentSchema(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    booking_id: str


class PayoutRequestSchema(BaseModel):
    amount: int = Field(..., gt=0)


class RefundSchema(BaseModel):
    booking_id: str
    amount: int = Field(..., gt=0)
    reason: Optional[str] = None
