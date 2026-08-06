from __future__ import annotations

from app.bots.poll.repository import PollRepository
from app.bots.poll.service import PollService
from app.bots.repository import BotsRepository
from app.modules.conversations.dependencies import get_conversations_service
from app.modules.messages.dependencies import get_messages_service
from app.modules.relationships.repository import RelationshipsRepository


def get_poll_service() -> PollService:
    return PollService(
        repo=PollRepository(),
        bots_repo=BotsRepository(),
        conversations=get_conversations_service(),
        messages=get_messages_service(),
        relationships=RelationshipsRepository(),
    )
