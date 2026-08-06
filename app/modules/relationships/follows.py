from __future__ import annotations

from app.core.errors import AppError
from app.db.models import ChannelDocument, RelationshipDocument, UserDocument
from app.db.models.relationship import RelationshipTargetType
from app.db.object_id import parse_object_id
from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.service import RelationshipService

_FOLLOW_TARGETS: set[RelationshipTargetType] = {"user", "channel"}


class FollowService:
    """One-directional feed interest (design §4).

    A follow is independent of any connection: following never creates or
    activates a connection, and a follow-back is a second, separate document.
    Public targets activate immediately; private ones stay pending until
    approved (§28).
    """

    def __init__(
        self,
        *,
        engine: RelationshipService | None = None,
        repo: RelationshipsRepository | None = None,
        channels_repo: ChannelsRepository | None = None,
        users_repo: UsersRepository | None = None,
    ) -> None:
        self.repo = repo or RelationshipsRepository()
        self.engine = engine or RelationshipService(repo=self.repo)
        self.channels_repo = channels_repo
        self.users_repo = users_repo

    async def requires_approval(
        self, *, target_type: RelationshipTargetType, target_id: str
    ) -> bool:
        if target_type == "user":
            user = await UserDocument.get(parse_object_id(target_id))
            if user is None:
                raise AppError(
                    code="USER_NOT_FOUND", message="User not found", status_code=404
                )
            return bool(user.is_private)

        channel = await ChannelDocument.get(parse_object_id(target_id))
        if channel is None:
            raise AppError(
                code="CHANNEL_NOT_FOUND", message="Channel not found", status_code=404
            )
        if channel.visibility == "private" or channel.join_policy == "closed":
            raise AppError(
                code="CHANNEL_FOLLOW_FORBIDDEN",
                message="This channel cannot be followed",
                status_code=403,
            )
        return channel.join_policy in {"approval", "invite_only"}

    async def follow(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
    ) -> RelationshipDocument:
        if target_type not in _FOLLOW_TARGETS:
            raise AppError(
                code="RELATIONSHIP_INVALID_TARGET",
                message="Follow targets a user or a channel",
                status_code=400,
            )
        pending = await self.requires_approval(
            target_type=target_type, target_id=target_id
        )
        follow = await self.engine.request(
            kind="follow",
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            status="pending" if pending else "active",
        )
        if follow.status == "active":
            await self._adjust_follower_count(
                target_type=follow.target_type,
                target_id=str(follow.target_id),
                delta=1,
            )
        return follow

    async def unfollow(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
    ) -> bool:
        doc = await self.repo.find_edge(
            kind="follow",
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
        )
        if doc is None:
            return False
        was_active = doc.status == "active"
        await self.engine.revoke(relationship_id=doc.str_id)
        if was_active:
            await self._adjust_follower_count(
                target_type=doc.target_type,
                target_id=str(doc.target_id),
                delta=-1,
            )
        return True

    async def accept(self, *, user_id: str, relationship_id: str) -> RelationshipDocument:
        """A private target approves an inbound follow request."""
        doc = await self._require(relationship_id)
        if not await self._can_respond(user_id=user_id, doc=doc):
            raise AppError(
                code="FOLLOW_FORBIDDEN",
                message="Not allowed to respond to this follow",
                status_code=403,
            )
        accepted = await self.engine.accept(
            relationship_id=doc.str_id, approved_by=user_id
        )
        if accepted.status == "active":
            await self._adjust_follower_count(
                target_type=accepted.target_type,
                target_id=str(accepted.target_id),
                delta=1,
            )
        return accepted

    async def decline(self, *, user_id: str, relationship_id: str) -> RelationshipDocument:
        doc = await self._require(relationship_id)
        if not await self._can_respond(user_id=user_id, doc=doc):
            raise AppError(
                code="FOLLOW_FORBIDDEN",
                message="Not allowed to respond to this follow",
                status_code=403,
            )
        return await self.engine.decline(
            relationship_id=doc.str_id, declined_by=user_id
        )

    async def _can_respond(
        self, *, user_id: str, doc: RelationshipDocument
    ) -> bool:
        if doc.target_type == "user":
            return str(doc.target_id) == str(user_id)
        if doc.target_type != "channel":
            return False
        channel = (
            await self.channels_repo.get_by_id(str(doc.target_id))
            if self.channels_repo is not None
            else await ChannelDocument.get(parse_object_id(str(doc.target_id)))
        )
        return (
            channel is not None
            and channel.owner.type == "user"
            and str(channel.owner.id) == str(user_id)
        )

    async def _adjust_follower_count(
        self,
        *,
        target_type: RelationshipTargetType,
        target_id: str,
        delta: int,
    ) -> None:
        if self.channels_repo is None:
            return
        channel_id = target_id
        if target_type == "user":
            if self.users_repo is None:
                return
            user = await self.users_repo.find_by_id(target_id)
            if user is None or user.main_channel_id is None:
                return
            channel_id = user.main_channel_id
        await self.channels_repo.adjust_follower_count(
            channel_id=channel_id,
            delta=delta,
        )

    async def is_following(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
    ) -> bool:
        doc = await self.repo.find_edge(
            kind="follow",
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
        )
        return doc is not None and doc.status == "active"

    async def list_following(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType | None = None,
        status: str = "active",
        limit: int = 100,
    ) -> list[RelationshipDocument]:
        return await self.repo.list_for_user(
            kind="follow",
            user_id=user_id,
            target_type=target_type,
            status=status,  # type: ignore[arg-type]
            limit=limit,
        )

    async def list_followers(
        self,
        *,
        target_type: RelationshipTargetType,
        target_id: str,
        status: str = "active",
        limit: int = 100,
    ) -> list[RelationshipDocument]:
        return await self.repo.list_for_target(
            kind="follow",
            target_type=target_type,
            target_id=target_id,
            status=status,  # type: ignore[arg-type]
            limit=limit,
        )

    async def _require(self, relationship_id: str) -> RelationshipDocument:
        doc = await self.repo.find_by_id(relationship_id)
        if doc is None or doc.kind != "follow":
            raise AppError(
                code="FOLLOW_NOT_FOUND", message="Follow not found", status_code=404
            )
        return doc
