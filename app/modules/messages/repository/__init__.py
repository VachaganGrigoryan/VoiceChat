from __future__ import annotations

from app.modules.messages.repository.base import BaseMessagesRepository
from app.modules.messages.repository.deletion import DeletionRepositoryMixin
from app.modules.messages.repository.helpers import ConversationListRow, conversation_id_for
from app.modules.messages.repository.history import HistoryRepositoryMixin
from app.modules.messages.repository.reactions import ReactionsRepositoryMixin
from app.modules.messages.repository.threads import ThreadsRepositoryMixin
from app.modules.messages.repository.write import WriteRepositoryMixin


class MessagesRepository(
    WriteRepositoryMixin,
    ThreadsRepositoryMixin,
    ReactionsRepositoryMixin,
    HistoryRepositoryMixin,
    DeletionRepositoryMixin,
    BaseMessagesRepository,
):
    pass


__all__ = [
    "ConversationListRow",
    "MessagesRepository",
    "conversation_id_for",
]
