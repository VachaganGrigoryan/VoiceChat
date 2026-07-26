from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.channels.service import ChannelService
from app.modules.messages.dependencies import get_messages_service


def get_channel_service() -> ChannelService:
    return ChannelService(
        repo=ChannelsRepository(),
        messages=get_messages_service(),
        users=UsersRepository(),
    )
