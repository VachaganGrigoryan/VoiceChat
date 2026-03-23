from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.infra.storage import build_storage_url, normalize_storage_name


class StickerAssetMapper:
    def resolve_storage_name(self, sticker: dict[str, Any]) -> str:
        return normalize_storage_name(sticker.get("storage"))

    def build_asset_url(self, *, storage_name: str, key: str) -> str:
        if storage_name == "s3":
            cdn_base_url = settings.cdn_base_url.strip()
            if cdn_base_url:
                return f"{cdn_base_url.rstrip('/')}/{key.lstrip('/')}"
        return build_storage_url(storage_name, key)

    def to_message_media(self, sticker: dict[str, Any]) -> dict[str, Any]:
        storage_name = self.resolve_storage_name(sticker)
        return {
            "storage": storage_name,
            "key": sticker["storage_key"],
            "url": self.build_asset_url(storage_name=storage_name, key=sticker["storage_key"]),
            "mime": sticker.get("mime_type", "image/webp"),
            "size_bytes": int(sticker.get("file_size", 0)),
            "duration_ms": None,
        }

    def to_catalog_urls(self, sticker: dict[str, Any]) -> dict[str, str]:
        storage_name = self.resolve_storage_name(sticker)
        return {
            "cdn_url": self.build_asset_url(storage_name=storage_name, key=sticker["storage_key"]),
            "thumb_url": self.build_asset_url(storage_name=storage_name, key=sticker["thumbnail_storage_key"]),
        }

