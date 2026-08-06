from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.conversations.repository import ConversationsRepository
from app.modules.notifications.dependencies import get_notifications_service
from app.modules.relationships.connections import ConnectionService
from app.modules.relationships.follows import FollowService
from app.modules.relationships.memberships import MembershipService
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.service import RelationshipService
from app.modules.realtime.presence import get_presence_backend


def get_relationship_service() -> RelationshipService:
    return RelationshipService(repo=RelationshipsRepository())


def get_connection_service() -> ConnectionService:
    repo = RelationshipsRepository()
    return ConnectionService(
        repo=repo,
        engine=RelationshipService(repo=repo),
        users_repo=UsersRepository(),
        presence_service=get_presence_backend(),
        conversations_repo=ConversationsRepository(),
        notifications_service=get_notifications_service(),
    )


def get_follow_service() -> FollowService:
    repo = RelationshipsRepository()
    return FollowService(
        repo=repo,
        engine=RelationshipService(repo=repo),
        channels_repo=ChannelsRepository(),
        users_repo=UsersRepository(),
    )


def get_membership_service() -> MembershipService:
    repo = RelationshipsRepository()
    return MembershipService(repo=repo, engine=RelationshipService(repo=repo))
