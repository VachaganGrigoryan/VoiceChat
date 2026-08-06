from __future__ import annotations

from app.modules.conversations.repository.base import BaseConversationsRepository
from app.modules.conversations.repository.invites import InvitesRepositoryMixin
from app.modules.conversations.repository.participants import (
    ParticipantsRepositoryMixin,
)
from app.modules.conversations.repository.read import ConversationsReadMixin
from app.modules.conversations.repository.write import ConversationsWriteMixin


class ConversationsRepository(
    ConversationsWriteMixin,
    ConversationsReadMixin,
    ParticipantsRepositoryMixin,
    InvitesRepositoryMixin,
    BaseConversationsRepository,
):
    """Composed conversations repository (write + read + participants + invites)."""


__all__ = ["ConversationsRepository"]
