import os
import unittest
from unittest.mock import patch, MagicMock

import tests.helpers  # noqa: F401  (sets JWT_SECRET)

from auth import authorization_service as auth_mod
from utilities.redis_connection import _InMemoryRedis

USER = {"user_id": "u1", "role": "CUSTOMER", "status": "ACTIVE", "phone": "9876543210"}


class OtpBypassTests(unittest.TestCase):
    def setUp(self):
        self.redis = _InMemoryRedis()
        self.svc = auth_mod.AuthorizationService()
        self.svc.modal = MagicMock()
        self.svc.modal.find_by_phone.return_value = USER
        patcher = patch.object(auth_mod, "get_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.svc._send_sms = MagicMock(return_value=("ok", None))

    def _real_otp(self):
        """Send an OTP and recover the 6-digit code from the SMS mock."""
        self.svc.send_otp({"phone": USER["phone"]}, None)
        return self.svc._send_sms.call_args[0][1]

    def _verify(self, otp):
        return self.svc.verify_otp({"phone": USER["phone"], "otp": otp}, None)

    def test_real_and_master_otp_both_work_when_bypass_on(self):
        with patch.dict(os.environ, {"OTP_BYPASS_ENABLED": "true", "ENVIRONMENT": "staging"}):
            status, _ = self._verify(self._real_otp())
            self.assertEqual(status, "success")
            status, _ = self._verify(auth_mod.MASTER_OTP)
            self.assertEqual(status, "success")

    def test_master_otp_works_by_default_on_staging(self):
        env = {k: v for k, v in os.environ.items() if k != "OTP_BYPASS_ENABLED"}
        env["ENVIRONMENT"] = "staging"
        with patch.dict(os.environ, env, clear=True):
            status, _ = self._verify(auth_mod.MASTER_OTP)
            self.assertEqual(status, "success")

    def test_master_otp_rejected_when_bypass_off(self):
        with patch.dict(os.environ, {"OTP_BYPASS_ENABLED": "false", "ENVIRONMENT": "staging"}):
            self._real_otp()
            with self.assertRaises(ValueError):
                self._verify(auth_mod.MASTER_OTP)

    def test_master_otp_rejected_in_production(self):
        with patch.dict(os.environ, {"OTP_BYPASS_ENABLED": "true", "ENVIRONMENT": "production"}):
            self._real_otp()
            with self.assertRaises(ValueError):
                self._verify(auth_mod.MASTER_OTP)

    def test_real_otp_works_when_bypass_off(self):
        with patch.dict(os.environ, {"OTP_BYPASS_ENABLED": "false", "ENVIRONMENT": "staging"}):
            status, _ = self._verify(self._real_otp())
            self.assertEqual(status, "success")


if __name__ == "__main__":
    unittest.main()
