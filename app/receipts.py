import os
import uuid

from fastapi import UploadFile

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
RECEIPTS_DIR = os.path.join(UPLOADS_DIR, "receipts")
os.makedirs(RECEIPTS_DIR, exist_ok=True)


def save_receipt(payment_id: int, file: UploadFile) -> str:
    """Persist an uploaded receipt image to disk. Returns the path to store on
    Payment.receipt_path, relative to UPLOADS_DIR (e.g. 'receipts/17_a1b2c3d4e5.jpg')."""
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    filename = f"{payment_id}_{uuid.uuid4().hex[:10]}{ext}"
    dest = os.path.join(RECEIPTS_DIR, filename)
    with open(dest, "wb") as f:
        f.write(file.file.read())
    return f"receipts/{filename}"
