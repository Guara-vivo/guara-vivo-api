import os
from pathlib import Path
from uuid import uuid4

LOCAL_STORAGE_PATH = os.getenv("LOCAL_STORAGE_PATH", "./data/storage")


def _get_extension(filename: str | None) -> str:
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def save_image(
    *,
    user_id: int,
    content: bytes,
    filename: str | None,
) -> str:
    extension = _get_extension(filename)
    relative_path = f"users/{user_id}/records/{uuid4().hex}{extension}"
    full_path = Path(LOCAL_STORAGE_PATH) / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    return f"/local-storage/{relative_path}"


PUBLIC_PREFIX = "/local-storage/"


def _resolve_inside_storage(public_url: str) -> Path | None:
    """Map a public URL to a file under LOCAL_STORAGE_PATH, or None if it points anywhere else.

    URLs come from clients (records.images is free text), so ``..`` segments and absolute
    paths must never reach the filesystem.
    """
    if not isinstance(public_url, str) or not public_url.startswith(PUBLIC_PREFIX):
        return None
    root = Path(LOCAL_STORAGE_PATH).resolve()
    candidate = (root / public_url[len(PUBLIC_PREFIX):]).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate


def delete_image(public_url: str) -> bool:
    full_path = _resolve_inside_storage(public_url)
    if full_path is None or not full_path.is_file():
        return False
    full_path.unlink()
    return True


def get_image_absolute_path(public_url: str) -> Path | None:
    full_path = _resolve_inside_storage(public_url)
    return full_path if full_path is not None and full_path.is_file() else None
