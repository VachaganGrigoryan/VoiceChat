from __future__ import annotations

from typing import Any, Protocol

from app.core.errors import AppError
from app.db.models import (
    BlockDocument,
    ConversationDocument,
    RelationshipDocument,
    SpaceDocument,
    UserDocument,
)
from app.db.models.notification import NotificationKind, NotificationResourceType
from app.db.models.relationship import RelationshipStatus
from app.db.object_id import parse_object_id
from app.modules.conversations.repository.helpers import dm_key_for
from app.modules.relationships.repository import RelationshipsRepository, pair_id_for
from app.modules.relationships.schemas import (
    ConnectionDirection,
    ConnectionExtras,
    ConnectionListItem,
    ConnectionState,
    PeerUserSummary,
    SharedConversationSummary,
    SharedSpaceSummary,
    to_relationship_view,
)
from app.modules.relationships.service import RelationshipService
from app.modules.users.avatar import build_user_avatar_payload


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> UserDocument | None: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


class ConversationsRepositoryProto(Protocol):
    async def get_by_dm_key(self, dm_key: str) -> ConversationDocument | None: ...


class NotificationsServiceProto(Protocol):
    async def create_notification(
        self,
        *,
        user_id: str,
        kind: NotificationKind,
        actor_user_id: str,
        resource_type: NotificationResourceType,
        resource_id: str,
        message_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any: ...


class ConnectionService:
    """Mutual user↔user contact — the Ping/Pong product concept (design §3).

    One document per canonical user pair; accepting makes the edge symmetric.
    Owns the connection lifecycle and DM permission gate.
    """

    def __init__(
        self,
        *,
        engine: RelationshipService | None = None,
        repo: RelationshipsRepository | None = None,
        users_repo: UsersRepositoryProto | None = None,
        presence_service: PresenceServiceProto | None = None,
        conversations_repo: ConversationsRepositoryProto | None = None,
        notifications_service: NotificationsServiceProto | None = None,
    ) -> None:
        self.repo = repo or RelationshipsRepository()
        self.engine = engine or RelationshipService(repo=self.repo)
        self.users_repo = users_repo
        self.presence_service = presence_service
        self.conversations_repo = conversations_repo
        self.notifications_service = notifications_service

    async def is_blocked(self, *, user_a: str, user_b: str) -> bool:
        return (
            await BlockDocument.find_one(
                {
                    "$or": [
                        {"blocker_id": str(user_a), "blocked_id": str(user_b)},
                        {"blocker_id": str(user_b), "blocked_id": str(user_a)},
                    ]
                }
            )
            is not None
        )

    async def request(self, *, from_user_id: str, to_user_id: str) -> RelationshipDocument:
        if self.users_repo is not None:
            target = await self.users_repo.find_by_id(to_user_id)
            if target is None:
                raise AppError(
                    code="USER_NOT_FOUND",
                    message="User not found",
                    status_code=404,
                )
        if await self.is_blocked(user_a=from_user_id, user_b=to_user_id):
            raise AppError(
                code="CONNECTION_BLOCKED",
                message="Cannot connect with this user",
                status_code=403,
            )
        existing = await self.repo.find_connection(
            user_a=from_user_id, user_b=to_user_id
        )
        doc = await self.engine.request(
            kind="connection",
            user_id=from_user_id,
            target_type="user",
            target_id=to_user_id,
        )
        if existing is None or existing.status in {"declined", "revoked"}:
            await self._create_notification(
                user_id=to_user_id,
                kind="connection_request",
                actor_user_id=from_user_id,
            )
        return doc

    async def accept(self, *, user_id: str, relationship_id: str) -> RelationshipDocument:
        doc = await self._require_counterparty(
            user_id=user_id, relationship_id=relationship_id
        )
        updated = await self.engine.accept(
            relationship_id=doc.str_id, approved_by=user_id
        )
        await self._create_notification(
            user_id=str(doc.user_id),
            kind="connection_accepted",
            actor_user_id=user_id,
        )
        return updated

    async def decline(self, *, user_id: str, relationship_id: str) -> RelationshipDocument:
        doc = await self._require_counterparty(
            user_id=user_id, relationship_id=relationship_id
        )
        return await self.engine.decline(
            relationship_id=doc.str_id, declined_by=user_id
        )

    async def revoke(self, *, user_id: str, relationship_id: str) -> RelationshipDocument:
        doc = await self._require_member(user_id=user_id, relationship_id=relationship_id)
        return await self.engine.revoke(relationship_id=doc.str_id)

    async def get(self, *, user_a: str, user_b: str) -> RelationshipDocument | None:
        return await self.repo.find_connection(user_a=user_a, user_b=user_b)

    async def are_connected(self, *, user_a: str, user_b: str) -> bool:
        doc = await self.repo.find_connection(user_a=user_a, user_b=user_b)
        return doc is not None and doc.status == "active"

    async def ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None:
        """Gate DM establishment on an active connection (§94)."""
        if await self.is_blocked(user_a=sender_id, user_b=receiver_id):
            raise AppError(
                code="CHAT_BLOCKED",
                message="Messaging is blocked for this user pair",
                status_code=403,
            )
        if not await self.are_connected(user_a=sender_id, user_b=receiver_id):
            raise AppError(
                code="CHAT_PERMISSION_REQUIRED",
                message="Active connection required before messaging",
                status_code=403,
            )

    async def list_connections(
        self,
        *,
        user_id: str,
        status: RelationshipStatus | None = "active",
        limit: int = 100,
    ) -> list[RelationshipDocument]:
        """Every connection touching ``user_id`` — either side of the pair.

        A connection is symmetric, so `user_id` may be the subject or the
        target; the query covers both rather than only the requester's rows.
        """
        query: dict = {
            "kind": "connection",
            "$or": [{"user_id": str(user_id)}, {"target_id": str(user_id)}],
        }
        if status:
            query["status"] = status
        return (
            await RelationshipDocument.find(query)
            .sort("-updated_at", "-_id")
            .limit(limit)
            .to_list()
        )

    async def list_connection_items(
        self,
        *,
        user_id: str,
        status: RelationshipStatus,
        direction: ConnectionDirection | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ConnectionListItem], str | None]:
        docs, next_cursor = await self.repo.list_connections_page(
            user_id=user_id,
            status=status,
            direction=direction,
            limit=limit,
            cursor=cursor,
        )
        items = [
            await self._to_connection_item(doc, user_id=user_id) for doc in docs
        ]
        return items, next_cursor

    def peer_of(self, doc: RelationshipDocument, *, user_id: str) -> str:
        return (
            str(doc.target_id)
            if str(doc.user_id) == str(user_id)
            else str(doc.user_id)
        )

    def direction_of(
        self, doc: RelationshipDocument, *, user_id: str
    ) -> ConnectionDirection:
        return "outgoing" if str(doc.user_id) == str(user_id) else "incoming"

    async def get_connection_state(
        self, *, viewer_user_id: str, peer_user_id: str
    ) -> ConnectionState:
        if str(viewer_user_id) == str(peer_user_id):
            return ConnectionState(
                can_ping=False,
                chat_allowed=False,
                connection_status="none",
            )

        doc = await self.repo.find_connection(
            user_a=viewer_user_id, user_b=peer_user_id
        )
        blocked_by_me, blocks_me = await self._get_block_state(
            viewer_user_id=viewer_user_id,
            peer_user_id=peer_user_id,
        )
        return self._connection_state_from_doc(
            viewer_user_id=viewer_user_id,
            doc=doc,
            blocked_by_me=blocked_by_me,
            blocks_me=blocks_me,
        )

    async def get_connection_states(
        self, *, viewer_user_id: str, peer_user_ids: list[str]
    ) -> dict[str, ConnectionState]:
        if not peer_user_ids:
            return {}

        unique_peer_ids = list(dict.fromkeys(str(peer_id) for peer_id in peer_user_ids))
        docs_by_pair_id = await self.repo.find_connections(
            user_id=viewer_user_id,
            peer_user_ids=unique_peer_ids,
        )
        states: dict[str, ConnectionState] = {}
        for peer_user_id in unique_peer_ids:
            blocked_by_me, blocks_me = await self._get_block_state(
                viewer_user_id=viewer_user_id,
                peer_user_id=peer_user_id,
            )
            states[peer_user_id] = self._connection_state_from_doc(
                viewer_user_id=viewer_user_id,
                doc=docs_by_pair_id.get(pair_id_for(viewer_user_id, peer_user_id)),
                blocked_by_me=blocked_by_me,
                blocks_me=blocks_me,
            )
        return states

    async def get_connection_extras(
        self, *, viewer_user_id: str, peer_user_id: str
    ) -> ConnectionExtras:
        if str(viewer_user_id) == str(peer_user_id):
            return ConnectionExtras()

        connection = await self.repo.find_connection(
            user_a=viewer_user_id, user_b=peer_user_id
        )
        if connection is None or connection.status != "active":
            return ConnectionExtras()

        conversation_id = await self._conversation_id(
            user_id=viewer_user_id, peer_user_id=peer_user_id
        )
        return ConnectionExtras(
            connection_timestamp=connection.activated_at or connection.updated_at,
            conversation_id=conversation_id,
            shared_conversations=await self._shared_conversations(
                user_id=viewer_user_id, peer_user_id=peer_user_id
            ),
            shared_spaces=await self._shared_spaces(
                user_id=viewer_user_id, peer_user_id=peer_user_id
            ),
        )

    async def shares_context(self, viewer_user_id: str, peer_user_id: str) -> bool:
        if str(viewer_user_id) == str(peer_user_id):
            return True
        if await self._shared_conversations(
            user_id=viewer_user_id, peer_user_id=peer_user_id
        ):
            return True
        return bool(
            await self._shared_spaces(
                user_id=viewer_user_id, peer_user_id=peer_user_id
            )
        )

    async def _create_notification(
        self,
        *,
        user_id: str,
        kind: NotificationKind,
        actor_user_id: str,
    ) -> None:
        if self.notifications_service is None:
            return
        await self.notifications_service.create_notification(
            user_id=str(user_id),
            kind=kind,
            actor_user_id=str(actor_user_id),
            resource_type="user",
            resource_id=str(actor_user_id),
            data={"peer_user_id": str(actor_user_id)},
        )

    async def _get_block_state(
        self, *, viewer_user_id: str, peer_user_id: str
    ) -> tuple[bool, bool]:
        docs = await BlockDocument.find(
            {
                "$or": [
                    {
                        "blocker_id": str(viewer_user_id),
                        "blocked_id": str(peer_user_id),
                    },
                    {
                        "blocker_id": str(peer_user_id),
                        "blocked_id": str(viewer_user_id),
                    },
                ]
            }
        ).to_list()
        blocked_by_me = any(
            str(doc.blocker_id) == str(viewer_user_id) for doc in docs
        )
        blocks_me = any(str(doc.blocker_id) == str(peer_user_id) for doc in docs)
        return blocked_by_me, blocks_me

    def _connection_state_from_doc(
        self,
        *,
        viewer_user_id: str,
        doc: RelationshipDocument | None,
        blocked_by_me: bool,
        blocks_me: bool,
    ) -> ConnectionState:
        relationship_id = doc.str_id if doc is not None else None
        if blocked_by_me or blocks_me:
            return ConnectionState(
                can_ping=False,
                chat_allowed=False,
                connection_status="blocked",
                relationship_id=relationship_id,
                blocked_by_me=blocked_by_me,
                blocks_me=blocks_me,
            )
        if doc is None:
            return ConnectionState(
                can_ping=True,
                chat_allowed=False,
                connection_status="none",
            )

        return ConnectionState(
            can_ping=doc.status in {"declined", "revoked"},
            chat_allowed=doc.status == "active",
            connection_status=doc.status,
            direction=self.direction_of(doc, user_id=viewer_user_id),
            relationship_id=doc.str_id,
        )

    async def _to_connection_item(
        self, doc: RelationshipDocument, *, user_id: str
    ) -> ConnectionListItem:
        peer_user_id = self.peer_of(doc, user_id=user_id)
        return ConnectionListItem(
            relationship=to_relationship_view(doc),
            peer=await self._peer_summary(peer_user_id),
            direction=self.direction_of(doc, user_id=user_id),
            conversation_id=await self._conversation_id(
                user_id=user_id, peer_user_id=peer_user_id
            ),
        )

    async def _peer_summary(self, user_id: str) -> PeerUserSummary:
        user = (
            await self.users_repo.find_by_id(user_id)
            if self.users_repo is not None
            else None
        )
        online = (
            await self.presence_service.is_online(user_id)
            if self.presence_service is not None
            else False
        )
        return PeerUserSummary(
            id=user_id,
            username=str(getattr(user, "username", "") or ""),
            display_name=getattr(user, "display_name", None),
            avatar=(
                build_user_avatar_payload(getattr(user, "avatar", None))
                if user is not None
                else None
            ),
            is_online=online,
        )

    async def _conversation_id(
        self, *, user_id: str, peer_user_id: str
    ) -> str | None:
        if self.conversations_repo is None:
            return None
        conversation = await self.conversations_repo.get_by_dm_key(
            dm_key_for(user_id, peer_user_id)
        )
        return conversation.str_id if conversation is not None else None

    async def _shared_conversations(
        self, *, user_id: str, peer_user_id: str
    ) -> list[SharedConversationSummary]:
        base_query = {
            "kind": "membership",
            "target_type": "conversation",
            "status": "active",
            "state.hidden": {"$ne": True},
        }
        my_memberships = await RelationshipDocument.find(
            {**base_query, "user_id": str(user_id)}
        ).to_list()
        my_conversation_ids = {
            str(membership.target_id) for membership in my_memberships
        }
        if not my_conversation_ids:
            return []

        peer_memberships = await RelationshipDocument.find(
            {
                **base_query,
                "user_id": str(peer_user_id),
                "target_id": {"$in": list(my_conversation_ids)},
            }
        ).to_list()
        shared_ids = [str(membership.target_id) for membership in peer_memberships]
        if not shared_ids:
            return []

        conversations = await ConversationDocument.find(
            {
                "_id": {"$in": [parse_object_id(item_id) for item_id in shared_ids]},
                "type": {"$ne": "dm"},
            }
        ).to_list()
        return [
            SharedConversationSummary(
                id=conversation.str_id,
                type=conversation.type,
                title=conversation.title,
            )
            for conversation in conversations
        ]

    async def _shared_spaces(
        self, *, user_id: str, peer_user_id: str
    ) -> list[SharedSpaceSummary]:
        base_query = {
            "kind": "membership",
            "target_type": "space",
            "status": "active",
        }
        my_memberships = await RelationshipDocument.find(
            {**base_query, "user_id": str(user_id)}
        ).to_list()
        my_space_ids = {str(membership.target_id) for membership in my_memberships}
        if not my_space_ids:
            return []

        peer_memberships = await RelationshipDocument.find(
            {
                **base_query,
                "user_id": str(peer_user_id),
                "target_id": {"$in": list(my_space_ids)},
            }
        ).to_list()
        shared_ids = [str(membership.target_id) for membership in peer_memberships]
        if not shared_ids:
            return []

        spaces = await SpaceDocument.find(
            {"_id": {"$in": [parse_object_id(item_id) for item_id in shared_ids]}}
        ).to_list()
        return [
            SharedSpaceSummary(id=space.str_id, name=space.name, slug=space.slug)
            for space in spaces
        ]

    async def _require_counterparty(
        self, *, user_id: str, relationship_id: str
    ) -> RelationshipDocument:
        """Only the invited side may accept/decline a pending connection."""
        doc = await self._require(relationship_id)
        if str(doc.target_id) != str(user_id):
            raise AppError(
                code="CONNECTION_FORBIDDEN",
                message="Not allowed to respond to this connection",
                status_code=403,
            )
        return doc

    async def _require_member(
        self, *, user_id: str, relationship_id: str
    ) -> RelationshipDocument:
        doc = await self._require(relationship_id)
        if str(user_id) not in {str(doc.user_id), str(doc.target_id)}:
            raise AppError(
                code="CONNECTION_FORBIDDEN",
                message="Not part of this connection",
                status_code=403,
            )
        return doc

    async def _require(self, relationship_id: str) -> RelationshipDocument:
        doc = await self.repo.find_by_id(relationship_id)
        if doc is None or doc.kind != "connection":
            raise AppError(
                code="CONNECTION_NOT_FOUND",
                message="Connection not found",
                status_code=404,
            )
        return doc


__all__ = ["ConnectionService", "pair_id_for"]
