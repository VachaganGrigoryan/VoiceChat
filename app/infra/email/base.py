from __future__ import annotations

from abc import ABC, abstractmethod


class EmailSender(ABC):
    @abstractmethod
    async def send_verification_code(self, *, to_email: str, code: str) -> None:
        raise NotImplementedError