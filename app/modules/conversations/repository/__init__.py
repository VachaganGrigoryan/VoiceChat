from __future__ import annotations

from app.modules.conversations.repository.base import BaseConversationsRepository
from app.modules.conversations.repository.participants import (
    ParticipantsRepositoryMixin,
)
from app.modules.conversations.repository.read import ConversationsReadMixin
from app.modules.conversations.repository.write import ConversationsWriteMixin


class ConversationsRepository(
    ConversationsWriteMixin,
    ConversationsReadMixin,
    ParticipantsRepositoryMixin,
    BaseConversationsRepository,
):
    """Composed conversations repository (write + read + participants)."""


__all__ = ["ConversationsRepository"]
