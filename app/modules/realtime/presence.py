from __future__ import annotations

from collections import defaultdict
from typing import Dict, Set


class PresenceRegistry:
    def __init__(self) -> None:
        self._user_sids: Dict[str, Set[str]] = defaultdict(set)

    def add(self, user_id: str, sid: str) -> bool:
        """
        Returns True if user became online for the first time.
        """
        was_online = self.is_online(user_id)
        self._user_sids[user_id].add(sid)
        return not was_online

    def remove(self, user_id: str, sid: str) -> bool:
        """
        Returns True if user became offline after removal.
        """
        if user_id not in self._user_sids:
            return False

        self._user_sids[user_id].discard(sid)

        if not self._user_sids[user_id]:
            del self._user_sids[user_id]
            return True

        return False

    def is_online(self, user_id: str) -> bool:
        return user_id in self._user_sids and len(self._user_sids[user_id]) > 0

    def get_online_user_ids(self) -> list[str]:
        return list(self._user_sids.keys())

    def get_online_count(self) -> int:
        return len(self._user_sids)

    def get_user_connection_count(self, user_id: str) -> int:
        return len(self._user_sids.get(user_id, set()))


presence_registry = PresenceRegistry()