from __future__ import annotations

import asyncio
from typing import Any

from app.db.models import CallDocument
from app.modules.calls.schemas import (
    CallDoc,
    CallHistoryItem,
    CallSession,
    IceServer,
)
from app.modules.calls.state import RECOVERABLE_CALL_STATUSES


class QueriesCallsMixin:
    async def clear_call_history(
        self,
        *,
        user_id: str,
        peer_user_id: str | None = None,
    ) -> tuple[int, int]:
        """
        Hard-deletes own call records (caller) and soft-hides peer call records (callee).
        Returns (deleted_count, hidden_count).
        """
        deleted_count = await self.repo.hard_delete_own_calls_in_history(
            user_id=user_id,
            peer_user_id=peer_user_id,
        )
        hidden_count = await self.repo.hide_peer_calls_for_user(
            user_id=user_id,
            peer_user_id=peer_user_id,
        )
        return deleted_count, hidden_count

    async def get_active_call(self, *, user_id: str) -> CallDocument | None:
        await self.expire_stale_calls()
        return await self.repo.find_live_call_for_user(
            user_id=user_id,
            statuses=RECOVERABLE_CALL_STATUSES,
        )

    async def get_participant_call(self, *, user_id: str, call_id: str) -> CallDocument:
        await self.expire_stale_calls()
        return await self._get_participant_call(user_id=user_id, call_id=call_id)

    async def get_active_call_session(self, *, user_id: str) -> CallSession | None:
        call_doc = await self.get_active_call(user_id=user_id)
        if call_doc is None:
            return None

        return await self.build_session(
            call_doc=call_doc,
            viewer_user_id=user_id,
            include_ice_servers=True,
        )

    async def list_history(
        self,
        *,
        user_id: str,
        peer_user_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[CallHistoryItem], str | None]:
        await self.expire_stale_calls()
        docs, next_cursor = await self.repo.list_history(
            user_id=user_id,
            peer_user_id=peer_user_id,
            limit=limit,
            cursor=cursor,
        )
        docs = [self._as_call_document(doc) for doc in docs]

        peer_user_ids = list(
            dict.fromkeys(
                self._peer_user_id_from_doc(call_doc=doc, viewer_user_id=user_id)
                for doc in docs
            )
        )
        users_task = asyncio.create_task(self.users_repo.find_by_ids(peer_user_ids))
        presence_task = asyncio.create_task(
            self._get_presence_map(user_ids=peer_user_ids)
        )
        users_by_id, online_by_id = await asyncio.gather(users_task, presence_task)

        items = [
            self._to_history_item(
                call_doc=doc,
                viewer_user_id=user_id,
                users_by_id=users_by_id,
                online_by_id=online_by_id,
            )
            for doc in docs
        ]
        return items, next_cursor

    async def build_session(
        self,
        *,
        call_doc: dict[str, Any] | CallDocument | CallDoc,
        viewer_user_id: str,
        include_ice_servers: bool = True,
    ) -> CallSession:
        model = self.to_call_doc(call_doc)
        peer_user_id = self._peer_user_id(call=model, viewer_user_id=viewer_user_id)
        peer = await self._build_peer_summary(peer_user_id)

        ice_servers: list[IceServer] = []
        if include_ice_servers and self.webrtc_service is not None:
            ice_servers = await self.webrtc_service.get_ice_servers()
        return CallSession(
            call=model,
            peer_user=peer,
            ice_servers=ice_servers,
        )
