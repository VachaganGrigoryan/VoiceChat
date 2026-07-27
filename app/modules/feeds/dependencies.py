from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.feeds.service import FeedService
from app.modules.messages.dependencies import get_messages_service
from app.modules.relationships.repository import RelationshipsRepository


def get_feeds_service() -> FeedService:
    users_repo = UsersRepository()
    return FeedService(
        channels_repo=ChannelsRepository(),
        messages_service=get_messages_service(),
        relationships_repo=RelationshipsRepository(),
        users_repo=users_repo,
    )
