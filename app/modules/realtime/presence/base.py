from __future__ import annotations

from abc import ABC, abstractmethod


class PresenceBackend(ABC):
    @abstractmethod
    async def add_connection(self, user_id: str, sid: str) -> bool:
        """
        Add a socket connection for the user.
        Returns True if user transitioned from offline -> online.
        """
        raise NotImplementedError

    @abstractmethod
    async def remove_connection(self, user_id: str, sid: str) -> bool:
        """
        Remove a socket connection for the user.
        Returns True if user transitioned from online -> offline.
        """
        raise NotImplementedError

    @abstractmethod
    async def is_online(self, user_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_online_user_ids(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def get_connection_count(self, user_id: str) -> int:
        raise NotImplementedError