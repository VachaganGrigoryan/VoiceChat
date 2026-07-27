from __future__ import annotations

from app.core.errors import AppError
from app.db.models import MessageContainerType, MessageDocument, SavedMessageDocument
from app.modules.authorization import AuthorizationService
from app.modules.authorization.permissions import MESSAGE_READ
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.repository.mappers import to_message_doc
from app.modules.messages.schemas import MessageDoc
from app.modules.saved.repository import SavedMessagesRepository
from app.modules.saved.schemas import SavedMessageView


class SavedMessagesService:
    """Private per-user message bookmarks; saves are never exposed to peers."""

    def __init__(
        self,
        repo: SavedMessagesRepository,
        messages_repo: MessagesRepository,
        authorization: AuthorizationService | None = None,
    ) -> None:
        self.repo = repo
        self.messages_repo = messages_repo
        self.authorization = authorization or AuthorizationService()

    async def save_message(
        self,
        *,
        user_id: str,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
    ) -> SavedMessageView:
        await self.authorization.require(
            user_id,
            MESSAGE_READ,
            container_type,
            container_id,
            message="Not allowed to access this message",
        )
        message = await self.messages_repo.get_by_id_in_container(
            container_type=container_type,
            container_id=container_id,
            message_id=message_id,
            user_id=user_id,
        )
        if message is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        saved = await self.repo.save(user_id=user_id, message_id=message_id)
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
            message = await self.messages_repo.get_by_id(
                message_id=str(saved.message_id)
            )
            if message is not None and not await self.authorization.can(
                user_id,
                MESSAGE_READ,
                message.container_type,
                message.container_id,
            ):
                message = None
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

    def _to_view(
        self,
        saved: SavedMessageDocument,
        *,
        message: MessageDoc | MessageDocument | None,
    ) -> SavedMessageView:
        message_view = (
            message
            if isinstance(message, MessageDoc) or message is None
            else to_message_doc(message)
        )
        return SavedMessageView(
            id=saved.str_id,
            user_id=str(saved.user_id),
            message_id=str(saved.message_id),
            container_type=message_view.container_type if message_view else None,
            container_id=message_view.container_id if message_view else None,
            saved_at=saved.saved_at,
            message=message_view,
        )
