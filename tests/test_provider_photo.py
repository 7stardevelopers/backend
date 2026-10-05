import os
import unittest
from unittest import mock

from tests.helpers import make_db, insert

from auth.authorization_service import AuthorizationService
from providers.providers_service import ProvidersService, _user_photo

BUCKET, REGION = "7sx-media-test", "ap-south-1"


def media_url(folder, user_id, name="selfie.jpg"):
    return f"https://{BUCKET}.s3.{REGION}.amazonaws.com/{folder}/{user_id}/{name}"


class ProviderPhotoTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"S3_MEDIA_BUCKET": BUCKET, "AWS_REGION_NAME": REGION})
        env.start()
        self.addCleanup(env.stop)
        self.conn = make_db()
        insert(self.conn, "users", user_id="u1", name="Ravi", role="PROVIDER")
        insert(self.conn, "users", user_id="c1", name="Asha", role="CUSTOMER")
        insert(self.conn, "providers", provider_id="p1", user_id="u1", status="PENDING")
        self.svc = ProvidersService()
        self.svc.notif = mock.Mock()

    def tearDown(self):
        self.conn.close()

    def _set(self, url, user_id="u1", role="PROVIDER"):
        return self.svc.set_my_photo({"_user_id": user_id, "_role": role, "photo_url": url}, self.conn)

    def test_first_photo_is_saved_and_shown_on_profile(self):
        url = media_url("profile", "u1")
        self._set(url)
        with mock.patch.object(self.svc.modal, "get_services", return_value=[]):
            _, profile = self.svc.get_my_profile({"_user_id": "u1", "_role": "PROVIDER"}, self.conn)
        self.assertEqual(profile["photo_url"], url)

    def test_photo_is_locked_after_first_set(self):
        self._set(media_url("profile", "u1"))
        with self.assertRaises(PermissionError) as ctx:
            self._set(media_url("profile", "u1", "second.jpg"))
        self.assertIn("locked", str(ctx.exception))

    def test_only_own_profile_uploads_accepted(self):
        for url in (media_url("profile", "someone-else"), media_url("documents", "u1"), "https://evil.example/x.jpg"):
            with self.assertRaises(ValueError):
                self._set(url)
        self.assertIsNone(_user_photo(self.conn, "u1"))

    def test_customers_cannot_use_provider_endpoint(self):
        with self.assertRaises(PermissionError):
            self._set(media_url("profile", "c1"), user_id="c1", role="CUSTOMER")

    def test_auth_me_cannot_change_worker_photo_but_customer_can(self):
        auth = AuthorizationService()
        with self.assertRaises(PermissionError):
            auth.update_profile({"_user_id": "u1", "_role": "PROVIDER", "photo_url": media_url("profile", "u1")}, self.conn)
        auth.update_profile({"_user_id": "u1", "_role": "PROVIDER", "name": "Ravi K"}, self.conn)  # name still editable
        auth.update_profile({"_user_id": "c1", "_role": "CUSTOMER", "photo_url": "https://cdn/x.jpg"}, self.conn)
        self.assertEqual(_user_photo(self.conn, "c1"), "https://cdn/x.jpg")

    def test_admin_reset_allows_a_retake(self):
        self._set(media_url("profile", "u1"))
        with self.assertRaises(PermissionError):
            self.svc.admin_reset_photo({"_role": "SUPPORT", "id": "p1"}, self.conn)
        self.svc.admin_reset_photo({"_role": "ADMIN", "id": "p1"}, self.conn)
        self.assertIsNone(_user_photo(self.conn, "u1"))
        self.assertEqual(self.svc.notif.send_push.call_args.kwargs["data"], {"type": "photo_reset"})
        self._set(media_url("profile", "u1", "retake.jpg"))
        self.assertTrue(_user_photo(self.conn, "u1").endswith("retake.jpg"))

    def test_approval_requires_a_photo(self):
        with self.assertRaises(ValueError):
            self.svc.admin_approve({"_role": "ADMIN", "id": "p1"}, self.conn)
        self._set(media_url("profile", "u1"))
        self.svc.admin_approve({"_role": "ADMIN", "id": "p1"}, self.conn)
        row = self.conn.exec_driver_sql("SELECT status FROM providers WHERE provider_id = 'p1'").fetchone()
        self.assertEqual(row.status, "APPROVED")


if __name__ == "__main__":
    unittest.main()
