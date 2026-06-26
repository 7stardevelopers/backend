from finance.finance_modal import FinanceMaster
from finance.finance_validator import ApprovePayoutSchema, ReportSchema
from utilities.common_table_elements import now_utc


class FinanceService:
    def __init__(self):
        self.modal = FinanceMaster()

    def _require_finance(self, role):
        if role not in ("ADMIN", "FINANCE"):
            raise PermissionError("Finance or Admin role required")

    def overview(self, obj, connection):
        self._require_finance(obj.pop("_role", None))
        obj.pop("_user_id", None)
        stats = self.modal.get_overview(connection)
        return "success", stats

    def payout_queue(self, obj, connection):
        self._require_finance(obj.pop("_role", None))
        obj.pop("_user_id", None)
        page = int(obj.get("page", 1))
        status = obj.get("status", "PENDING")
        payouts = self.modal.list_payout_queue(connection, status=status, page=page)
        return "success", payouts

    def approve_payout(self, obj, connection):
        self._require_finance(obj.pop("_role", None))
        obj.pop("_user_id", None)
        payout_id = obj.pop("id", None) or obj.pop("payout_id", None)
        data = ApprovePayoutSchema(**obj)
        fields = {"status": data.status}
        if data.notes:
            fields["notes"] = data.notes
        self.modal.update_payout(connection, payout_id, fields)
        if data.status == "PROCESSED":
            from utilities.db_connection import metadata
            payout_t = metadata.tables["payout_requests"]
            payout = connection.execute(payout_t.select().where(payout_t.c.payout_id == payout_id)).fetchone()
            if payout:
                from providers.providers_modal import ProvidersMaster
                ProvidersMaster().update_wallet(connection, payout["provider_id"], payout["amount"], "debit")
        return "success", {"message": f"Payout {data.status.lower()}"}

    def export_report(self, obj, connection):
        self._require_finance(obj.pop("_role", None))
        obj.pop("_user_id", None)
        data = ReportSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        report = self.modal.get_report(connection, from_date=data.from_date, to_date=data.to_date)
        return "success", report
