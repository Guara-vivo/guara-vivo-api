import os
import magic
from fastapi import HTTPException


ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def validate_image_file(filename: str, file_bytes: bytes) -> str:
    """
    Validate image file by extension and magic bytes.
    Returns detected MIME type or raises HTTPException.
    """
    # Check extension
    _, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type {ext} not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # Check magic bytes
    try:
        mime = magic.from_buffer(file_bytes, mime=True)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not validate file: {str(e)}"
        )
    
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File MIME type {mime} not allowed. Only JPEG, PNG, WebP accepted."
        )
    
    return mime
