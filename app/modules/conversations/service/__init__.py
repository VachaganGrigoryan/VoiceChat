from __future__ import annotations

from app.modules.conversations.service.base import (
    BaseConversationsService,
    PingsPermissionProto,
)
from app.modules.conversations.service.create import CreateConversationsMixin
from app.modules.conversations.service.invites import InvitesServiceMixin
from app.modules.conversations.service.participants import ParticipantsServiceMixin
from app.modules.conversations.service.power import PowerFeaturesServiceMixin
from app.modules.conversations.service.read import ReadConversationsMixin


class ConversationsService(
    CreateConversationsMixin,
    ReadConversationsMixin,
    ParticipantsServiceMixin,
    PowerFeaturesServiceMixin,
    InvitesServiceMixin,
    BaseConversationsService,
):
    """Composed conversations service (create + read + participants + invites)."""


__all__ = [
    "ConversationsService",
    "BaseConversationsService",
    "PingsPermissionProto",
]
