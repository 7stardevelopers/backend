from typing import Optional
from pydantic import BaseModel


class SubscribeSchema(BaseModel):
    plan_id: str
    payment_id: Optional[str] = None
