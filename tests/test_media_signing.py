import os
import unittest
from unittest import mock

from media.media_service import sign_media_urls, strip_media_signatures, is_own_upload

BUCKET, REGION = "7sx-media-test", "ap-south-1"
PREFIX = f"https://{BUCKET}.s3.{REGION}.amazonaws.com/"


class MediaSigningTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"S3_MEDIA_BUCKET": BUCKET, "AWS_REGION_NAME": REGION})
        env.start()
        self.addCleanup(env.stop)
        client = mock.patch("media.media_service.boto3.client")
        self.s3 = client.start().return_value
        self.addCleanup(client.stop)
        self.s3.generate_presigned_url.side_effect = (
            lambda op, Params, ExpiresIn: f"{PREFIX}{Params['Key']}?X-Amz-Signature=sig"
        )

    def test_signs_media_urls_in_nested_data(self):
        data = {
            "photo_url": PREFIX + "profile/u1/a.jpg",
            "booking": {"proof_photos": [PREFIX + "proof/u1/b.jpg"]},
        }
        out = sign_media_urls(data)
        self.assertEqual(out["photo_url"], PREFIX + "profile/u1/a.jpg?X-Amz-Signature=sig")
        self.assertEqual(out["booking"]["proof_photos"], [PREFIX + "proof/u1/b.jpg?X-Amz-Signature=sig"])
        self.s3.generate_presigned_url.assert_any_call(
            "get_object", Params={"Bucket": BUCKET, "Key": "profile/u1/a.jpg"}, ExpiresIn=3600,
        )

    def test_leaves_other_values_alone(self):
        data = {"a": "https://evil.example/x.jpg", "b": None, "c": 5, "d": PREFIX + "x.jpg?already=signed"}
        self.assertEqual(sign_media_urls(data), data)
        self.s3.generate_presigned_url.assert_not_called()

    def test_strip_restores_plain_url(self):
        signed = PREFIX + "profile/u1/a.jpg?X-Amz-Signature=sig"
        out = strip_media_signatures({"photo_url": signed, "other": "https://x.example/?q=1"})
        self.assertEqual(out["photo_url"], PREFIX + "profile/u1/a.jpg")
        self.assertEqual(out["other"], "https://x.example/?q=1")
        self.assertTrue(is_own_upload(out["photo_url"], "u1", "profile"))


if __name__ == "__main__":
    unittest.main()
