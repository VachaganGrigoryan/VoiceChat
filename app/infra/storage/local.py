from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.core.config import settings
from app.infra.storage.base import Storage, StoredFile


class LocalStorage(Storage):
    async def save(self, *, filename: str, content: bytes, mime: str) -> StoredFile:
        # Keep extension if present
        ext = Path(filename).suffix.lower() or ".bin"
        file_id = uuid.uuid4().hex
        stored_name = f"{file_id}{ext}"

        os.makedirs(settings.upload_dir, exist_ok=True)
        full_path = os.path.join(settings.upload_dir, stored_name)

        with open(full_path, "wb") as f:
            f.write(content)

        return StoredFile(
            storage="local",
            key=full_path,
            url=self.get_file_url(stored_name),
            size_bytes=len(content),
            mime=mime,
        )

    def get_file_url(self, key: str) -> str:
        return f"/media/{key}"