from __future__ import annotations

from app.core.config import settings
from app.infra.storage.base import Storage
from app.infra.storage.keys import build_avatar_key, build_voice_key, build_image_key
from app.infra.storage.local import LocalStorage
from app.infra.storage.s3 import S3Storage


def get_storage() -> Storage:
    if settings.storage_provider == "s3":
        return S3Storage()
    return LocalStorage()


def build_storage_url(storage_name: str, key: str) -> str:
    if storage_name == "local":
        return LocalStorage().get_file_url(key)
    if storage_name == "s3":
        return S3Storage().get_file_url(key)
    raise ValueError(...)


class MediaStorageService:
    def __init__(self):
        self.storage = get_storage()

    async def save_avatar(self, *, user_id: str, filename: str, content: bytes, mime: str):
        key = build_avatar_key(user_id, filename)
        return await self.storage.save(
            filename=filename,
            content=content,
            mime=mime,
            key=key,
        )

    async def save_voice(self, *, user_id: str, filename: str, content: bytes, mime: str):
        key = build_voice_key(user_id, filename)
        return await self.storage.save(
            filename=filename,
            content=content,
            mime=mime,
            key=key,
        )

    async def save_image(self, *, user_id: str, filename: str, content: bytes, mime: str):
        key = build_image_key(user_id, filename)
        return await self.storage.save(
            filename=filename,
            content=content,
            mime=mime,
            key=key,
        )