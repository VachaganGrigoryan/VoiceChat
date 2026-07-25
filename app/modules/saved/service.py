from __future__ import annotations

from app.core.errors import AppError
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.repository.mappers import to_message_doc
from app.modules.saved.repository import SavedMessagesRepository
from app.modules.saved.schemas import SavedMessageView


class SavedMessagesService:
    """Private per-user message bookmarks; saves are never exposed to peers."""

    def __init__(
        self,
        repo: SavedMessagesRepository,
        messages_repo: MessagesRepository,
    ) -> None:
        self.repo = repo
        self.messages_repo = messages_repo

    async def save_message(
        self, *, user_id: str, conversation_id: str, message_id: str
    ) -> SavedMessageView:
        # The message must be accessible to the caller (in-conversation and not
        # hidden for them) before it can be bookmarked.
        message = await self.messages_repo.get_by_id_in_container(
            container_type="conversation",
            container_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        if message is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        saved = await self.repo.save(
            user_id=user_id,
            message_id=message_id,
            conversation_id=conversation_id,
        )
        return self._to_view(saved, message=to_message_doc(message))

    async def list_saved(
        self, *, user_id: str, limit: int
    ) -> list[SavedMessageView]:
        if limit < 1 or limit > 100:
            raise AppError(
                code="INVALID_LIMIT",
                message="limit must be between 1 and 100",
                status_code=400,
            )
        saved_items = await self.repo.list_for_user(user_id=user_id, limit=limit)
        views: list[SavedMessageView] = []
        for saved in saved_items:
            message = await self.messages_repo.get_by_id_in_container(
                container_type="conversation",
                container_id=saved.conversation_id,
                message_id=str(saved.message_id),
                user_id=user_id,
            )
            views.append(
                self._to_view(
                    saved,
                    message=to_message_doc(message) if message is not None else None,
                )
            )
        return views

    async def remove_saved(self, *, user_id: str, message_id: str) -> None:
        removed = await self.repo.remove(user_id=user_id, message_id=message_id)
        if not removed:
            raise AppError(
                code="SAVED_MESSAGE_NOT_FOUND",
                message="Saved message not found",
                status_code=404,
            )

    def _to_view(self, saved, *, message) -> SavedMessageView:
        return SavedMessageView(
            id=saved.str_id,
            user_id=str(saved.user_id),
            message_id=str(saved.message_id),
            conversation_id=saved.conversation_id,
            saved_at=saved.saved_at,
            message=message,
        )
