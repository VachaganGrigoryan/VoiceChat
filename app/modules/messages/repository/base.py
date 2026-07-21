from __future__ import annotations

from typing import Any

from app.db.models import MessageDocument
from app.db.mongo import get_db
from app.db.repository import BaseRepository


class BaseMessagesRepository(BaseRepository[MessageDocument]):
    model = MessageDocument

    @property
    def col(self) -> Any:
        # Alias for `raw`: the Mongo-shaped message operations (aggregation,
        # optimistic-lock CAS, dotted $set, cursor pagination) read `self.col`.
        return self.raw

    @property
    def db(self) -> Any:
        return get_db()

    def _as_message_document(
        self, doc: MessageDocument | dict[str, Any]
    ) -> MessageDocument:
        if isinstance(doc, MessageDocument):
            return doc
        return MessageDocument.model_validate(doc)
