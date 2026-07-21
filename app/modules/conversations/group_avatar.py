from __future__ import annotations

from typing import Any

from fastapi import UploadFile

from app.core.errors import AppError
from app.infra.storage import build_storage_url

# Mirrors the user-avatar limits in app/modules/users/service.py.
ALLOWED_GROUP_IMAGE_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_GROUP_IMAGE_SIZE_BYTES = 5 * 1024 * 1024


def build_group_avatar_payload(image: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rebuild the public URL of a stored group image (mirrors user avatar)."""
    if image is None:
        return None

    payload = dict(image)
    storage = payload.get("storage")
    key = payload.get("key")

    if storage and key:
        payload["url"] = build_storage_url(storage, key)

    return payload


async def read_group_image_upload(file: UploadFile) -> tuple[bytes, str]:
    """Validate and read an uploaded group image, returning (bytes, content_type)."""
    content_type = (file.content_type or "").lower().strip()
    if content_type not in ALLOWED_GROUP_IMAGE_CONTENT_TYPES:
        raise AppError(
            code="UNSUPPORTED_IMAGE_TYPE",
            message="Group image must be jpeg, png, or webp",
            status_code=400,
        )

    data = await file.read()
    if not data:
        raise AppError(
            code="EMPTY_FILE",
            message="Uploaded file is empty",
            status_code=400,
        )

    if len(data) > MAX_GROUP_IMAGE_SIZE_BYTES:
        raise AppError(
            code="IMAGE_TOO_LARGE",
            message="Group image must be at most 5 MB",
            status_code=400,
        )

    return data, content_type
