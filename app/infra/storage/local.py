from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.core.config import settings
from app.infra.storage.base import Storage, StoredFile


class LocalStorage(Storage):
    name = "local"

    def _normalize_key(self, key: str) -> str:
        normalized = key.replace("\\", "/").lstrip("/")
        if normalized.startswith("../") or "/../" in f"/{normalized}":
            raise ValueError("invalid storage key")
        return normalized

    def _full_path(self, key: str) -> str:
        normalized = self._normalize_key(key)
        return os.path.join(settings.upload_dir, normalized)

    async def save(
            self,
            *,
            filename: str,
            content: bytes,
            mime: str,
            key: str | None = None,
    ) -> StoredFile:
        ext = Path(filename).suffix.lower() or ".bin"

        if key is None:
            key = f"{uuid.uuid4().hex}{ext}"
        else:
            key = self._normalize_key(key)
            if not Path(key).suffix:
                key = f"{key}{ext}"

        full_path = self._full_path(key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "wb") as f:
            f.write(content)

        return StoredFile(
            storage="local",
            key=key,
            url=self.get_file_url(key),
            size_bytes=len(content),
            mime=mime,
        )

    async def delete(self, key: str) -> None:
        full_path = self._full_path(key)
        try:
            os.remove(full_path)
        except FileNotFoundError:
            return

    def get_file_url(self, key: str) -> str:
        return f"/media/{self._normalize_key(key)}"

    async def read(self, key: str) -> bytes:
        full_path = self._full_path(key)
        try:
            with open(full_path, "rb") as f:
                return f.read()
        except FileNotFoundError as exc:
            raise FileNotFoundError(self._normalize_key(key)) from exc
