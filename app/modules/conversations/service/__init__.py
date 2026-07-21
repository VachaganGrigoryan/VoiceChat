from __future__ import annotations

from app.modules.conversations.service.base import (
    BaseConversationsService,
    PingsPermissionProto,
)
from app.modules.conversations.service.create import CreateConversationsMixin
from app.modules.conversations.service.participants import ParticipantsServiceMixin
from app.modules.conversations.service.read import ReadConversationsMixin


class ConversationsService(
    CreateConversationsMixin,
    ReadConversationsMixin,
    ParticipantsServiceMixin,
    BaseConversationsService,
):
    """Composed conversations service (create + read + participants)."""


__all__ = [
    "ConversationsService",
    "BaseConversationsService",
    "PingsPermissionProto",
]
