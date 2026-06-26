from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.db.models import MessageDocument
from app.db.repository import BaseRepository
from app.modules.messages.repository.helpers import message_participants


class BaseMessagesRepository(BaseRepository[MessageDocument]):
    model = MessageDocument

    @property
    def col(self) -> Any:
        # Alias for `raw`: the Mongo-shaped message operations (aggregation,
        # optimistic-lock CAS, dotted $set, cursor pagination) read `self.col`.
        return self.raw

    def _as_message_document(
        self, doc: MessageDocument | dict[str, Any]
    ) -> MessageDocument:
        if isinstance(doc, MessageDocument):
            return doc
        return MessageDocument.model_validate(doc)

    def _assert_message_participant(
        self, *, message: MessageDocument, user_id: str
    ) -> None:
        if user_id not in message_participants(message):
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
