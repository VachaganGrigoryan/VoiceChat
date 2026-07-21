from __future__ import annotations

from typing import Any, Protocol

from app.core.errors import AppError
from app.modules.conversations.repository import ConversationsRepository


class PingsPermissionProto(Protocol):
    async def ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None: ...

    async def get_contact_state(self, *, viewer_user_id: str, peer_user_id: str) -> Any: ...


class UsersRepositoryProto(Protocol):
    async def find_by_ids(self, user_ids: list[str]) -> dict[str, Any]: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


class BaseConversationsService:
    def __init__(
        self,
        repo: ConversationsRepository,
        pings_service: PingsPermissionProto | None = None,
        users_repo: UsersRepositoryProto | None = None,
        presence_service: PresenceServiceProto | None = None,
    ) -> None:
        self.repo = repo
        self.pings_service = pings_service
        self.users_repo = users_repo
        self.presence_service = presence_service

    def _require_distinct(self, *, user_id: str, peer_user_id: str) -> None:
        if str(user_id) == str(peer_user_id):
            raise AppError(
                code="INVALID_CONVERSATION",
                message="Cannot start a conversation with yourself",
                status_code=400,
            )

    async def _ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None:
        if self.pings_service is None:
            return
        await self.pings_service.ensure_can_message(
            sender_id=str(sender_id), receiver_id=str(receiver_id)
        )

    def _peer_id(self, conversation: Any, *, user_id: str) -> str | None:
        others = [
            str(pid) for pid in conversation.participant_ids if str(pid) != str(user_id)
        ]
        return others[0] if others else None
