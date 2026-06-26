ALLOWED_DOC_TYPES = {
    "AADHAAR_FRONT", "AADHAAR_BACK", "PAN", "POLICE_VERIFICATION", "CERTIFICATE", "PROFILE_PHOTO",
}

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
}

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
UPLOAD_URL_EXPIRY_SECS = 300  # 5 minutes
