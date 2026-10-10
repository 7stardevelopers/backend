"""Rapido-style rules for worker money. Amounts are paise; override per environment."""
import os

# A worker who owes the platform this much (fees on cash jobs) gets no new jobs
# until they pay it from the worker app.
CASH_DUES_LIMIT = int(os.environ.get("CASH_DUES_LIMIT", "50000"))      # ₹500

# Customer cancels after a worker accepted and the grace period ran out.
CANCEL_FEE = int(os.environ.get("CANCEL_FEE", "5000"))                  # ₹50
CANCEL_GRACE_MIN = int(os.environ.get("CANCEL_GRACE_MIN", "5"))

# Scheduled payouts: on these IST weekdays, every worker with at least PAYOUT_MIN
# available gets a payout queued for admin to transfer.
PAYOUT_MIN = int(os.environ.get("PAYOUT_MIN", "20000"))                 # ₹200
PAYOUT_DAYS = [d.strip().upper() for d in os.environ.get("PAYOUT_DAYS", "MON,THU").split(",") if d.strip()]
PAYOUT_HOUR_IST = int(os.environ.get("PAYOUT_HOUR_IST", "10"))
