from __future__ import annotations

from app.modules.messages.repository import MessagesRepository
from app.modules.saved.repository import SavedMessagesRepository
from app.modules.saved.service import SavedMessagesService


def get_saved_messages_service() -> SavedMessagesService:
    return SavedMessagesService(
        repo=SavedMessagesRepository(),
        messages_repo=MessagesRepository(),
    )
