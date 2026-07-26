from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.feeds.service import FeedsService
from app.modules.messages.dependencies import get_messages_service


def get_feeds_service() -> FeedsService:
    users_repo = UsersRepository()
    return FeedsService(
        channels_repo=ChannelsRepository(),
        messages_service=get_messages_service(),
        users_repo=users_repo,
    )
