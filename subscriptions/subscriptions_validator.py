from pydantic import BaseModel


class SubscribeSchema(BaseModel):
    plan_id: str
    payment_id: str
