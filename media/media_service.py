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


def _media_bucket():
    return os.environ.get("S3_MEDIA_BUCKET", "7sx-media-staging"), os.environ.get("AWS_REGION_NAME", "ap-south-1")


def _media_prefix() -> str:
    bucket, region = _media_bucket()
    return f"https://{bucket}.s3.{region}.amazonaws.com/"


def is_own_upload(url: str, user_id: str, folder: str) -> bool:
    """True if url points at an object this user uploaded into folder via /media/presign."""
    prefix = f"{_media_prefix()}{folder}/{user_id}/"
    return isinstance(url, str) and url.startswith(prefix) and ".." not in url


def _map_media_strings(data, fn, prefix):
    if isinstance(data, str):
        return fn(data) if data.startswith(prefix) else data
    if isinstance(data, dict):
        return {k: _map_media_strings(v, fn, prefix) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_map_media_strings(v, fn, prefix) for v in data]
    return data


def sign_media_urls(data, expires: int = 3600):
    """The media bucket is private: swap every plain media-bucket URL in an API
    response for a short-lived presigned GET URL the apps' <Image> can load.
    The DB keeps the plain URL (see strip_media_signatures)."""
    bucket, region = _media_bucket()
    prefix = _media_prefix()
    s3 = None

    def sign(url):
        nonlocal s3
        if "?" in url:
            return url
        if s3 is None:
            s3 = boto3.client("s3", region_name=region, config=Config(signature_version='s3v4'))
        return s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": url[len(prefix):]}, ExpiresIn=expires,
        )

    return _map_media_strings(data, sign, prefix)


def strip_media_signatures(data):
    """Turn signed media URLs a client echoes back into the plain stored form."""
    return _map_media_strings(data, lambda url: url.split("?", 1)[0], _media_prefix())
