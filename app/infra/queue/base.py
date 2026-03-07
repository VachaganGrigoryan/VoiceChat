from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class JobQueue(ABC):
    @abstractmethod
    async def publish(self, *, queue_name: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError