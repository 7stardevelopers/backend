from finance.finance_modal import FinanceMaster
from finance.finance_validator import ApprovePayoutSchema, ReportSchema
from utilities.common_table_elements import now_utc


# target status -> statuses it may be reached from
PAYOUT_TRANSITIONS = {
    "APPROVED":  ("PENDING",),
    "REJECTED":  ("PENDING", "APPROVED"),
    "PROCESSED": ("PENDING", "APPROVED"),
}


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
        payout = self.modal.get_payout(connection, payout_id)
        if not payout:
            raise ValueError("Payout not found")
        allowed_from = PAYOUT_TRANSITIONS.get(data.status, ())
        if payout["status"] not in allowed_from:
            raise ValueError(f"Cannot move payout from {payout['status']} to {data.status}")

        fields = {"status": data.status}
        if data.notes:
            fields["notes"] = data.notes
        # Conditional on the status we just checked, so two admins can't both
        # process (and double-debit) the same payout.
        if not self.modal.update_payout(connection, payout_id, fields, expected_status=payout["status"]):
            raise ValueError("Payout was updated by someone else — refresh and try again")
        if data.status == "PROCESSED":
            from providers.providers_modal import ProvidersMaster
            ProvidersMaster().update_wallet(connection, payout["provider_id"], payout["amount"], "debit")
        return "success", {"message": f"Payout {data.status.lower()}"}

    def bulk_update_payouts(self, obj, connection):
        """PATCH /finance/payouts/bulk {payout_ids: [...], status, notes?} — e.g. mark a
        whole scheduled batch PROCESSED after the bank transfer. Each id goes through
        approve_payout, so the same transitions and wallet debits apply."""
        role = obj.pop("_role", None)
        user_id = obj.pop("_user_id", None)
        self._require_finance(role)
        ids = obj.get("payout_ids") or []
        if not isinstance(ids, list) or not ids:
            raise ValueError("payout_ids must be a non-empty list")
        if len(ids) > 200:
            raise ValueError("At most 200 payouts at a time")
        done, failed = [], []
        for pid in ids:
            try:
                self.approve_payout({"_role": role, "_user_id": user_id, "id": pid,
                                     "status": obj.get("status"), "notes": obj.get("notes")}, connection)
                done.append(pid)
            except ValueError as e:
                failed.append({"payout_id": pid, "error": str(e)})
        return "success", {"updated": done, "failed": failed}

    def export_report(self, obj, connection):
        self._require_finance(obj.pop("_role", None))
        obj.pop("_user_id", None)
        data = ReportSchema(**{k: v for k, v in obj.items() if not k.startswith("_")})
        report = self.modal.get_report(connection, from_date=data.from_date, to_date=data.to_date)
        return "success", report
