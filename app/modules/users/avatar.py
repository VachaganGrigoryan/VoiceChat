from __future__ import annotations

from typing import Any

from app.infra.storage import build_storage_url


def build_user_avatar_payload(avatar: dict[str, Any] | None) -> dict[str, Any] | None:
    if avatar is None:
        return None

    payload = dict(avatar)
    storage = payload.get("storage")
    key = payload.get("key")

    if storage and key:
        payload["url"] = build_storage_url(storage, key)

    return payload
