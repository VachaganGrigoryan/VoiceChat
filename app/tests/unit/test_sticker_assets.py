from __future__ import annotations

from app.core.config import settings
from app.modules.stickers.assets import StickerAssetMapper


def test_sticker_asset_mapper_uses_persisted_storage_for_local_urls():
    mapper = StickerAssetMapper()

    media = mapper.to_message_media(
        {
            "storage": "local",
            "storage_key": "stickers/pack/sticker/original.webp",
            "mime_type": "image/webp",
            "file_size": 128,
        }
    )

    assert media["storage"] == "local"
    assert media["url"] == "/media/stickers/pack/sticker/original.webp"


def test_sticker_asset_mapper_uses_cdn_for_s3_urls(monkeypatch):
    mapper = StickerAssetMapper()
    monkeypatch.setattr(settings, "cdn_base_url", "https://cdn.example.com")

    urls = mapper.to_catalog_urls(
        {
            "storage": "s3",
            "storage_key": "stickers/pack/sticker/original.webp",
            "thumbnail_storage_key": "stickers/pack/sticker/thumb.webp",
        }
    )

    assert urls["cdn_url"] == "https://cdn.example.com/stickers/pack/sticker/original.webp"
    assert urls["thumb_url"] == "https://cdn.example.com/stickers/pack/sticker/thumb.webp"


def test_sticker_asset_mapper_falls_back_to_configured_storage(monkeypatch):
    mapper = StickerAssetMapper()
    monkeypatch.setattr(settings, "storage_provider", "local")

    media = mapper.to_message_media(
        {
            "storage_key": "stickers/legacy/original.webp",
            "mime_type": "image/webp",
            "file_size": 64,
        }
    )

    assert media["storage"] == "local"
    assert media["url"] == "/media/stickers/legacy/original.webp"

