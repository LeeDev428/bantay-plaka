from pathlib import PurePath

from django.core.exceptions import ValidationError


MAX_DOCUMENT_SIZE = 5 * 1024 * 1024
ALLOWED_DOCUMENT_TYPES = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".pdf": "pdf",
}


def validate_uploaded_document(upload):
    extension = PurePath(upload.name).suffix.lower()
    if extension not in ALLOWED_DOCUMENT_TYPES:
        raise ValidationError("Upload a JPG, PNG, or PDF file.")
    if upload.size > MAX_DOCUMENT_SIZE:
        raise ValidationError("Each document must be 5 MB or smaller.")

    position = upload.tell()
    upload.seek(0)
    signature = upload.read(8)
    upload.seek(position)

    valid_signature = {
        ".jpg": signature.startswith(b"\xff\xd8\xff"),
        ".jpeg": signature.startswith(b"\xff\xd8\xff"),
        ".png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
        ".pdf": signature.startswith(b"%PDF-"),
    }[extension]
    if not valid_signature:
        raise ValidationError("The file contents do not match the selected document type.")
