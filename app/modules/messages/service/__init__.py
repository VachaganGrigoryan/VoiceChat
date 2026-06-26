"""Messages service, split per operation cluster.

A thin facade composes per-concern mixins (mirrors `messages/repository/`).
`MessagesService` and `SendMessageResult` are re-exported so existing
`from app.modules.messages.service import MessagesService` imports keep working.
"""

from __future__ import annotations

from app.modules.messages.service.base import (
    BaseMessagesService,
    SendMessageResult,
)
from app.modules.messages.service.create import CreateMessagesMixin
from app.modules.messages.service.delete import DeleteMessagesMixin
from app.modules.messages.service.reactions import ReactionsMessagesMixin
from app.modules.messages.service.read import ReadMessagesMixin
from app.modules.messages.service.update import UpdateMessagesMixin


class MessagesService(
    CreateMessagesMixin,
    ReadMessagesMixin,
    UpdateMessagesMixin,
    DeleteMessagesMixin,
    ReactionsMessagesMixin,
    BaseMessagesService,
):
    pass


__all__ = [
    "MessagesService",
    "SendMessageResult",
]
