import os
import boto3
from botocore.client import Config
from utilities.common_table_elements import new_uuid
from documents.documents_config import ALLOWED_CONTENT_TYPES

# "profile": the worker's registration selfie (set once via POST /providers/me/photo).
ALLOWED_FOLDERS = {"documents", "proof", "profile"}


class MediaService:
    def presign(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if not user_id:
            raise PermissionError("Authentication required")

        content_type = obj.get("content_type", "image/jpeg")
        folder = obj.get("folder", "documents")

        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Invalid content_type. Allowed: {list(ALLOWED_CONTENT_TYPES)}")
        if folder not in ALLOWED_FOLDERS:
            raise ValueError(f"Invalid folder. Allowed: {list(ALLOWED_FOLDERS)}")

        ext = ALLOWED_CONTENT_TYPES[content_type]
        bucket = os.environ.get("S3_MEDIA_BUCKET", "7sx-media-staging")
        bucket_region = os.environ.get("AWS_REGION_NAME", "ap-south-1")
        # Scope every upload to its owner so a URL can't be passed off as someone
        # else's document (checked in providers_service.set_documents).
        key = f"{folder}/{user_id}/{new_uuid()}{ext}"

        s3 = boto3.client("s3", region_name=bucket_region, config=Config(signature_version='s3v4'))
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=300,
        )
        object_url = f"https://{bucket}.s3.{bucket_region}.amazonaws.com/{key}"

        return "success", {"upload_url": upload_url, "object_url": object_url}


def is_own_upload(url: str, user_id: str, folder: str) -> bool:
    """True if url points at an object this user uploaded into folder via /media/presign."""
    bucket = os.environ.get("S3_MEDIA_BUCKET", "7sx-media-staging")
    region = os.environ.get("AWS_REGION_NAME", "ap-south-1")
    prefix = f"https://{bucket}.s3.{region}.amazonaws.com/{folder}/{user_id}/"
    return isinstance(url, str) and url.startswith(prefix) and ".." not in url
