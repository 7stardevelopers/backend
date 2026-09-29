from auth.authorization_modal import UsersMaster
from referrals.referrals_modal import ReferralsMaster
from referrals.referrals_validator import RedeemReferralSchema

REFERRAL_BONUS = 50  # coins credited to BOTH the referrer and the new signup


class ReferralsService:
    def __init__(self):
        self.modal = ReferralsMaster()
        self.users = UsersMaster()

    def my_code(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        user = self.users.find_by_id(connection, user_id)
        if not user:
            raise ValueError("User not found")

        code = user.get("referral_code")
        if not code:
            # Backfills users created before referral codes existed.
            code = self.users._generate_unique_referral_code(connection)
            self.users.update(connection, user_id, {"referral_code": code})

        return "success", {
            "code": code,
            "referral_count": self.modal.count_referrals(connection, user_id),
            "total_earned": self.modal.sum_earned(connection, user_id),
            "coins_balance": user.get("coins_balance", 0),
        }

    def redeem(self, obj, connection):
        user_id = obj.pop("_user_id")
        obj.pop("_role", None)
        data = RedeemReferralSchema(**obj)

        user = self.users.find_by_id(connection, user_id)
        if not user:
            raise ValueError("User not found")
        if user.get("referred_by"):
            raise ValueError("You've already used a referral code")

        referrer = self.users.find_by_referral_code(connection, data.code.strip().upper())
        if not referrer:
            raise ValueError("Invalid referral code")
        if str(referrer["user_id"]) == str(user_id):
            raise ValueError("You can't use your own referral code")

        self.modal.set_referred_by(connection, user_id, referrer["user_id"])
        self.modal.credit(connection, user_id, REFERRAL_BONUS, "REFERRAL_BONUS")
        self.modal.credit(connection, referrer["user_id"], REFERRAL_BONUS, "REFERRAL_BONUS")

        return "success", {
            "message": f"{REFERRAL_BONUS} coins credited to your wallet!",
            "coins_credited": REFERRAL_BONUS,
        }
