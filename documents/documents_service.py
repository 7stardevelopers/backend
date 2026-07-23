import io
import base64
import os
import boto3

from documents.documents_modal import DocumentsMaster
from documents.documents_config import ALLOWED_DOC_TYPES, ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE_BYTES
from providers.providers_modal import ProvidersMaster
from utilities.common_table_elements import now_utc


class DocumentsService:
    def __init__(self):
        self.modal = DocumentsMaster()
        self.provider_modal = ProvidersMaster()

    def upload(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")

        doc_type = obj.get("doc_type", "").upper()
        content_type = obj.get("content_type", "image/jpeg")
        file_content_b64 = obj.get("file_content")

        if doc_type not in ALLOWED_DOC_TYPES:
            raise ValueError(f"Invalid doc_type. Allowed: {list(ALLOWED_DOC_TYPES)}")
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Invalid content_type. Allowed: {list(ALLOWED_CONTENT_TYPES)}")
        if not file_content_b64:
            raise ValueError("file_content is required")

        provider = self.provider_modal.find_by_user_id(connection, user_id)
        if not provider:
            raise ValueError("Provider profile not found")

        try:
            file_bytes = base64.b64decode(file_content_b64)
        except Exception:
            raise ValueError("file_content is not valid base64")

        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            raise ValueError(f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB limit")

        ext = ALLOWED_CONTENT_TYPES[content_type]
        bucket = os.environ.get("S3_DOCUMENTS_BUCKET", "7starexperts-documents-staging")
        bucket_region = os.environ.get("AWS_REGION_NAME", "ap-south-1")
        key = f"providers/{provider['provider_id']}/{doc_type}{ext}"
        file_url = f"https://{bucket}.s3.{bucket_region}.amazonaws.com/{key}"

        s3 = boto3.client("s3", region_name=bucket_region)
        s3.upload_fileobj(
            io.BytesIO(file_bytes),
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

        doc = self.modal.upsert_by_type(connection, provider["provider_id"], doc_type, file_url)
        return "success", {"document_id": doc["document_id"], "file_url": file_url}

    def list_mine(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        provider = self.provider_modal.find_by_user_id(connection, user_id)
        if not provider:
            return "success", []
        docs = self.modal.list_by_provider(connection, provider["provider_id"])
        return "success", docs

    def admin_list(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        provider_id = obj.get("providerId") or obj.get("provider_id")
        docs = self.modal.list_by_provider(connection, provider_id)
        return "success", docs

    def admin_fetch_content(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        document_id = obj.get("id")
        doc = self.modal.get_one(connection, document_id)
        if not doc:
            raise ValueError("Document not found")
        bucket = os.environ.get("S3_DOCUMENTS_BUCKET", "7starexperts-documents-staging")
        bucket_region = os.environ.get("AWS_REGION_NAME", "ap-south-1")
        prefix = f"https://{bucket}.s3.{bucket_region}.amazonaws.com/"
        key = doc["file_url"].replace(prefix, "")
        s3 = boto3.client("s3", region_name=bucket_region)
        response = s3.get_object(Bucket=bucket, Key=key)
        file_bytes = response["Body"].read()
        content_type = response.get("ContentType", "image/jpeg")
        file_b64 = base64.b64encode(file_bytes).decode("utf-8")
        return "success", {"content_type": content_type, "file_content": file_b64}

    def admin_verify(self, obj, connection):
        role = obj.pop("_role", None)
        admin_user_id = obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        document_id = obj.get("id") or obj.get("document_id")
        status = obj.get("status")
        if status not in ("VERIFIED", "REJECTED"):
            raise ValueError("status must be VERIFIED or REJECTED")
        fields = {"status": status, "verified_at": now_utc(), "verified_by": admin_user_id}
        if status == "REJECTED":
            fields["rejection_reason"] = obj.get("rejection_reason", "")
        self.modal.update(connection, document_id, fields)
        return "success", {"message": f"Document {status.lower()}"}
