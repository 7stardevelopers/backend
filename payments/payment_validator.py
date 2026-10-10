from pydantic import BaseModel, Field, model_validator
from typing import Optional

# Amounts are integer paise.


class CreateOrderSchema(BaseModel):
    booking_id: Optional[str] = None
    plan_id: Optional[str] = None
    # Ignored — amount comes from the DB and currency is always INR.
    amount: Optional[int] = None
    currency: Optional[str] = None

    @model_validator(mode="after")
    def _one_target(self):
        if bool(self.booking_id) == bool(self.plan_id):
            raise ValueError("Send either booking_id or plan_id")
        return self


class VerifyPaymentSchema(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    booking_id: Optional[str] = None
    plan_id: Optional[str] = None


class PayoutRequestSchema(BaseModel):
    amount: int = Field(..., gt=0)


class RefundSchema(BaseModel):
    booking_id: Optional[str] = None
    payment_id: Optional[str] = None
    amount: Optional[int] = Field(None, gt=0)  # None = everything still refundable
    reason: Optional[str] = Field(None, max_length=200)
    # Also take the refunded amount back from the worker's wallet (completed jobs).
    deduct_from_worker: bool = False

    @model_validator(mode="after")
    def _one_target(self):
        if not (self.booking_id or self.payment_id):
            raise ValueError("Send booking_id or payment_id")
        return self
