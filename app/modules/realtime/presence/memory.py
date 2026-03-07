from __future__ import annotations

from collections import defaultdict
from typing import Dict, Set

from app.modules.realtime.presence.base import PresenceBackend


class InMemoryPresenceBackend(PresenceBackend):
    def __init__(self) -> None:
        self._user_sids: Dict[str, Set[str]] = defaultdict(set)

    async def add_connection(self, user_id: str, sid: str) -> bool:
        was_online = self.is_online(user_id)
        self._user_sids[user_id].add(sid)
        return not was_online

    async def remove_connection(self, user_id: str, sid: str) -> bool:
        if user_id not in self._user_sids:
            return False

        self._user_sids[user_id].discard(sid)

        if not self._user_sids[user_id]:
            del self._user_sids[user_id]
            return True

        return False

    async def is_online(self, user_id: str) -> bool:
        return user_id in self._user_sids and len(self._user_sids[user_id]) > 0

    async def get_online_user_ids(self) -> list[str]:
        return list(self._user_sids.keys())

    async def get_connection_count(self, user_id: str) -> int:
        return len(self._user_sids.get(user_id, set()))