from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.relationships.connections import ConnectionService
from app.modules.relationships.follows import FollowService
from app.modules.relationships.memberships import MembershipService
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.service import RelationshipService


def get_relationship_service() -> RelationshipService:
    return RelationshipService(repo=RelationshipsRepository())


def get_connection_service() -> ConnectionService:
    repo = RelationshipsRepository()
    return ConnectionService(repo=repo, engine=RelationshipService(repo=repo))


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
