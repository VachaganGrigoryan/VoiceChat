from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StoredFile:
    storage: str          # "local" | "s3"
    key: str              # path/key in storage
    url: str              # playable URL
    size_bytes: int
    mime: str


class Storage(ABC):
    @abstractmethod
    async def save(self, *, filename: str, content: bytes, mime: str) -> StoredFile:
        raise NotImplementedError

    @abstractmethod
    def get_file_url(self, key: str) -> str:
        raise NotImplementedError