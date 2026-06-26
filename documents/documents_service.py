import os
import boto3

from documents.documents_modal import DocumentsMaster
from documents.documents_config import ALLOWED_DOC_TYPES, ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE_BYTES, UPLOAD_URL_EXPIRY_SECS
from providers.providers_modal import ProvidersMaster
from utilities.common_table_elements import now_utc


class DocumentsService:
    def __init__(self):
        self.modal = DocumentsMaster()
        self.provider_modal = ProvidersMaster()

    def generate_upload_url(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")

        doc_type = obj.get("doc_type", "").upper()
        content_type = obj.get("content_type", "image/jpeg")

        if doc_type not in ALLOWED_DOC_TYPES:
            raise ValueError(f"Invalid doc_type. Allowed: {ALLOWED_DOC_TYPES}")
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Invalid content_type")

        provider = self.provider_modal.find_by_user_id(connection, user_id)
        if not provider:
            raise ValueError("Provider profile not found")

        ext = ALLOWED_CONTENT_TYPES[content_type]
        bucket = os.environ.get("S3_DOCUMENTS_BUCKET", "7starexperts-documents-staging")
        key = f"providers/{provider['provider_id']}/{doc_type}{ext}"

        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION_NAME", "ap-south-1"))
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=UPLOAD_URL_EXPIRY_SECS,
        )

        doc = self.modal.create(connection, {
            "provider_id": provider["provider_id"],
            "doc_type": doc_type,
            "file_url": f"https://{bucket}.s3.ap-south-1.amazonaws.com/{key}",
            "status": "PENDING",
        })

        return "success", {"upload_url": url, "document_id": doc["document_id"], "key": key}

    def confirm_upload(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "PROVIDER":
            raise PermissionError("Provider role required")
        document_id = obj.get("document_id")
        doc = self.modal.get_one(connection, document_id)
        if not doc:
            raise ValueError("Document not found")
        provider = self.provider_modal.find_by_user_id(connection, user_id)
        if str(doc["provider_id"]) != str(provider["provider_id"]):
            raise PermissionError("Access denied")
        self.modal.update(connection, document_id, {"status": "PENDING"})
        return "success", {"message": "Upload confirmed"}

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
