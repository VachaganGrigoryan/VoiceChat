from __future__ import annotations

from app.core.config import settings
from app.infra.storage.base import Storage, UploadTarget
from app.infra.storage.local import LocalStorage
from app.infra.storage.s3 import S3Storage
from app.infra.storage.keys import storage_key_builder, FolderKind


def normalize_storage_name(provider: str | None = None) -> str:
    storage_provider = (provider or settings.storage_provider or "").strip().lower()
    if storage_provider in {"local", "s3"}:
        return storage_provider
    return "local"


def get_storage(provider: str | None = None) -> Storage:
    storage_provider = normalize_storage_name(provider)
    if storage_provider == "s3":
        return S3Storage()
    return LocalStorage()


def build_storage_url(storage_name: str, key: str) -> str:
    storage_name = normalize_storage_name(storage_name)
    if storage_name == "local":
        return LocalStorage().get_file_url(key)
    if storage_name == "s3":
        return S3Storage().get_file_url(key)
    raise ValueError(...)


__all__ = [
    "get_storage",
    "normalize_storage_name",
    "build_storage_url",
    "storage_key_builder",
    "FolderKind",
    "UploadTarget",
]
