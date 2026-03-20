from __future__ import annotations

from app.core.config import settings
from app.infra.storage.base import Storage
from app.infra.storage.local import LocalStorage
from app.infra.storage.s3 import S3Storage
from app.infra.storage.keys import storage_key_builder, FolderKind

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


__all__ = ["get_storage", "build_storage_url", "storage_key_builder", "FolderKind"]