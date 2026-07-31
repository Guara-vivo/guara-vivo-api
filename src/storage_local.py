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


def delete_image(public_url: str) -> None:
    relative = public_url.replace("/local-storage/", "", 1)
    full_path = Path(LOCAL_STORAGE_PATH) / relative
    if full_path.exists():
        full_path.unlink()


def get_image_absolute_path(public_url: str) -> Path | None:
    relative = public_url.replace("/local-storage/", "", 1)
    full_path = Path(LOCAL_STORAGE_PATH) / relative
    return full_path if full_path.exists() else None