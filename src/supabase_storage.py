import logging
import os
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "frontend-uploads")
MAX_UPLOAD_FILE_BYTES = int(os.getenv("MAX_UPLOAD_FILE_BYTES", "5242880"))


EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _public_prefix() -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/"


def is_own_storage_url(url: str, user_id: int) -> bool:
    """True if `url` is a file this API stored for `user_id`.

    Without Supabase configured (local dev/tests) there is nothing to compare against.
    """
    if not SUPABASE_URL:
        return True
    prefix = f"{_public_prefix()}users/{user_id}/records/"
    return url.startswith(prefix) and ".." not in url[len(prefix):]


def _require_storage_config() -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for uploads")


async def upload_public_image(
    *,
    user_id: int,
    content: bytes,
    content_type: str,
) -> str:
    """Store an already-validated image. `content_type` must be the detected MIME type."""
    _require_storage_config()

    extension = EXTENSION_BY_MIME[content_type]
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

    return f"{_public_prefix()}{object_path}"


async def delete_uploaded_images(public_urls: list[str]) -> None:
    """Best-effort removal of files that no record references. Never raises."""
    prefix = _public_prefix()
    paths = [url[len(prefix):] for url in public_urls if url.startswith(prefix)]
    if not paths or not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                "DELETE",
                f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}",
                headers=headers,
                json={"prefixes": paths},
            )
        if response.status_code not in {200, 204}:
            logger.warning("could not delete %s orphan image(s): status %s", len(paths), response.status_code)
    except Exception:
        logger.exception("could not delete %s orphan image(s)", len(paths))

