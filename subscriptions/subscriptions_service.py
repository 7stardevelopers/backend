from subscriptions.subscriptions_modal import SubscriptionsMaster
from subscriptions.subscriptions_validator import SubscribeSchema, CreatePlanSchema, UpdatePlanSchema


class SubscriptionsService:
    def __init__(self):
        self.modal = SubscriptionsMaster()

    def list_plans(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        plans = self.modal.list_plans(connection)
        return "success", plans

    def subscribe(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "CUSTOMER":
            raise PermissionError("Only customers can subscribe")
        data = SubscribeSchema(**obj)
        plan = self.modal.get_plan(connection, data.plan_id)
        if not plan:
            raise ValueError("Plan not found")
        sub = self.modal.create_subscription(connection, user_id, data.plan_id, data.payment_id)
        return "created", {**sub, "plan": plan}

    def get_mine(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "CUSTOMER":
            raise PermissionError("Customer role required")
        sub = self.modal.get_active_subscription(connection, user_id)
        if not sub:
            return "success", None
        plan = self.modal.get_plan(connection, sub["plan_id"])
        return "success", {**sub, "plan": plan}

    def admin_create_plan(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        data = CreatePlanSchema(**obj)
        plan = self.modal.create_plan(connection, data.model_dump())
        return "created", plan

    def admin_update_plan(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        plan_id = obj.pop("id", None) or obj.pop("plan_id", None)
        data = UpdatePlanSchema(**obj)
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        self.modal.update_plan(connection, plan_id, fields)
        return "success", {"message": "Plan updated"}

    def cancel(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "CUSTOMER":
            raise PermissionError("Customer role required")
        sub = self.modal.cancel_subscription(connection, user_id)
        return "success", {"message": "Subscription cancelled"}
