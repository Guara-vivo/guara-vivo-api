import mimetypes
import os
from pathlib import Path
from uuid import uuid4

import httpx


SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "frontend-uploads")
MAX_UPLOAD_FILE_BYTES = int(os.getenv("MAX_UPLOAD_FILE_BYTES", "5242880"))


def _get_extension(filename: str | None, content_type: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix

    guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
    if guessed in {".jpe", ".jpeg"}:
        return ".jpg"
    if guessed in {".jpg", ".png", ".webp", ".gif"}:
        return guessed
    return ".jpg"


def _require_storage_config() -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for uploads")


async def upload_public_image(
    *,
    user_id: int,
    content: bytes,
    filename: str | None,
    content_type: str,
) -> str:
    _require_storage_config()

    extension = _get_extension(filename, content_type)
    object_path = f"users/{user_id}/records/{uuid4().hex}{extension}"
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{object_path}"

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "false",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(upload_url, headers=headers, content=content)

    if response.status_code not in {200, 201}:
        detail = response.text[:300]
        raise RuntimeError(f"Supabase upload failed with status {response.status_code}: {detail}")

    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/{object_path}"
