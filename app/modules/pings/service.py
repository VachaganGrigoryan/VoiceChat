from __future__ import annotations

from typing import Any, Protocol

from bson import ObjectId
from fastapi import HTTPException

from app.core.errors import AppError
from app.db.models import PingDocument, UserDocument
from app.modules.pings.repository import PingsRepository, pair_id_for
from app.modules.pings.schemas import (
    ContactExtras,
    ContactListItem,
    PingListItem,
    PingResponse,
    PeerUserSummary,
    ContactState,
    SharedConversationSummary,
    SharedSpaceSummary,
)
from app.modules.users.avatar import build_user_avatar_payload


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> UserDocument | None: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


class ConversationsRepositoryProto(Protocol):
    async def get_by_dm_key(self, dm_key: str): ...


class NotificationsServiceProto(Protocol):
    async def create_notification(
        self,
        *,
        user_id: str,
        kind: str,
        source_type: str | None = None,
        source_id: str | None = None,
        conversation_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any: ...


class PingsService:
    def __init__(
        self,
        *,
        pings_repo: PingsRepository,
        users_repo: UsersRepositoryProto,
        presence_service: PresenceServiceProto | None = None,
        conversations_repo: ConversationsRepositoryProto | None = None,
        notifications_service: NotificationsServiceProto | None = None,
    ) -> None:
        self.pings_repo = pings_repo
        self.users_repo = users_repo
        self.presence_service = presence_service
        self.conversations_repo = conversations_repo
        self.notifications_service = notifications_service

    async def _emit_ping_notification(
        self, *, user_id: str, kind: str, peer_user_id: str
    ) -> None:
        """Record a connection event as a generic notification for ``user_id``.

        Blocked users are never notified; callers pass the recipient explicitly.
        """
        if self.notifications_service is None:
            return
        await self.notifications_service.create_notification(
            user_id=str(user_id),
            kind=kind,
            source_type="ping",
            data={"peer_user_id": str(peer_user_id)},
        )

    def _doc_value(self, doc: object, key: str, default: Any = None) -> Any:
        if isinstance(doc, dict):
            value = doc.get("_id", default) if key == "id" else doc.get(key, default)
        else:
            value = getattr(doc, key, default)
        return str(value) if isinstance(value, ObjectId) else value

    async def send_ping(self, *, from_user_id: str, to_user_id: str) -> PingResponse:
        if from_user_id == to_user_id:
            raise HTTPException(status_code=400, detail="Cannot ping yourself")

        target = await self.users_repo.find_by_id(to_user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if await self.pings_repo.is_blocked(user_a=from_user_id, user_b=to_user_id):
            raise AppError(
                code="PING_BLOCKED",
                message="Cannot ping this user",
                status_code=403,
            )

        existing = await self.pings_repo.find_by_pair_id(
            pair_id_for(from_user_id, to_user_id)
        )
        if existing:
            existing_status = self._doc_value(existing, "status")
            if existing_status == "accepted":
                raise HTTPException(
                    status_code=409, detail="Chat permission already granted"
                )
            if existing_status == "pending":
                return self._to_ping_response(existing)
            if existing_status in {"cancelled", "declined"}:
                reopened = await self.pings_repo.reopen_ping(
                    ping_id=self._doc_value(existing, "id", ""),
                    from_user_id=from_user_id,
                    to_user_id=to_user_id,
                )
                assert reopened is not None
                await self._emit_ping_notification(
                    user_id=to_user_id,
                    kind="ping_received",
                    peer_user_id=from_user_id,
                )
                return self._to_ping_response(reopened)

        doc = await self.pings_repo.create_ping(
            from_user_id=from_user_id, to_user_id=to_user_id
        )
        await self._emit_ping_notification(
            user_id=to_user_id,
            kind="ping_received",
            peer_user_id=from_user_id,
        )
        return self._to_ping_response(doc)

    async def accept_ping(self, *, user_id: str, ping_id: str) -> PingResponse:
        ping = await self.pings_repo.find_by_id(ping_id)
        if not ping:
            raise HTTPException(status_code=404, detail="Ping not found")
        if self._doc_value(ping, "to_user_id") != user_id:
            raise HTTPException(
                status_code=403, detail="Not allowed to accept this ping"
            )
        if self._doc_value(ping, "status") != "pending":
            raise HTTPException(status_code=409, detail="Ping is not pending")

        updated = await self.pings_repo.update_status(
            ping_id=ping_id, status="accepted"
        )
        assert updated is not None
        await self._emit_ping_notification(
            user_id=self._doc_value(ping, "from_user_id"),
            kind="ping_accepted",
            peer_user_id=user_id,
        )
        return self._to_ping_response(updated)

    async def decline_ping(self, *, user_id: str, ping_id: str) -> PingResponse:
        ping = await self.pings_repo.find_by_id(ping_id)
        if not ping:
            raise HTTPException(status_code=404, detail="Ping not found")
        if self._doc_value(ping, "to_user_id") != user_id:
            raise HTTPException(
                status_code=403, detail="Not allowed to decline this ping"
            )
        if self._doc_value(ping, "status") != "pending":
            raise HTTPException(status_code=409, detail="Ping is not pending")

        updated = await self.pings_repo.update_status(
            ping_id=ping_id, status="declined"
        )
        assert updated is not None
        await self._emit_ping_notification(
            user_id=self._doc_value(ping, "from_user_id"),
            kind="ping_declined",
            peer_user_id=user_id,
        )
        return self._to_ping_response(updated)

    async def list_incoming(
        self,
        *,
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[PingListItem], str | None]:
        docs, next_cursor = await self.pings_repo.list_incoming(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )
        items = [
            await self._to_list_item(doc, user_id=user_id, incoming=True)
            for doc in docs
        ]
        return items, next_cursor

    async def list_outgoing(
        self,
        *,
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[PingListItem], str | None]:
        docs, next_cursor = await self.pings_repo.list_outgoing(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )
        items = [
            await self._to_list_item(doc, user_id=user_id, incoming=False)
            for doc in docs
        ]
        return items, next_cursor

    async def has_chat_permission(self, *, user_a: str, user_b: str) -> bool:
        return await self.pings_repo.has_accepted_permission(
            user_a=user_a, user_b=user_b
        )

    async def ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None:
        if await self.pings_repo.is_blocked(user_a=sender_id, user_b=receiver_id):
            raise AppError(
                code="CHAT_BLOCKED",
                message="Messaging is blocked for this user pair",
                status_code=403,
            )

        allowed = await self.has_chat_permission(user_a=sender_id, user_b=receiver_id)
        if not allowed:
            raise AppError(
                code="CHAT_PERMISSION_REQUIRED",
                message="Accepted ping required before messaging",
                status_code=403,
            )

    async def cancel_ping(self, *, user_id: str, ping_id: str):
        """User can cancel only owned pings"""
        ping = await self.pings_repo.find_by_id(ping_id)
        if not ping:
            raise HTTPException(status_code=404, detail="Ping not found")
        if self._doc_value(ping, "from_user_id") != user_id:
            raise HTTPException(
                status_code=403, detail="Not allowed to cancel this ping"
            )
        if self._doc_value(ping, "status") != "pending":
            raise HTTPException(status_code=409, detail="Ping is not pending")

        updated = await self.pings_repo.update_status(
            ping_id=ping_id, status="cancelled"
        )
        assert updated is not None
        await self._emit_ping_notification(
            user_id=self._doc_value(ping, "to_user_id"),
            kind="ping_cancelled",
            peer_user_id=user_id,
        )
        return self._to_ping_response(updated)

    async def block_user(self, *, user_id: str, peer_user_id: str):
        if user_id == peer_user_id:
            raise AppError(
                code="INVALID_BLOCK_TARGET",
                message="Cannot block yourself",
                status_code=400,
            )
        doc = await self.pings_repo.block_pair(
            user_a=user_id,
            user_b=peer_user_id,
            by_user_id=user_id,
        )
        # Only the blocker is notified; the blocked user receives nothing.
        await self._emit_ping_notification(
            user_id=user_id,
            kind="user_blocked",
            peer_user_id=peer_user_id,
        )
        return self._to_ping_response(doc)

    async def unblock_user(self, *, user_id: str, peer_user_id: str):
        if user_id == peer_user_id:
            raise AppError(
                code="INVALID_BLOCK_TARGET",
                message="Cannot unblock yourself",
                status_code=400,
            )
        doc = await self.pings_repo.unblock_pair(
            user_a=user_id,
            user_b=peer_user_id,
            by_user_id=user_id,
        )
        return self._to_ping_response(doc)

    async def delete_ping_for_pair(self, *, user_id: str, peer_user_id: str) -> bool:
        return await self.pings_repo.delete_pair(user_a=user_id, user_b=peer_user_id)

    async def list_blocked(self, *, user_id: str):
        docs = await self.pings_repo.list_blocked(user_id=user_id)
        return [self._to_ping_response(doc) for doc in docs]

    async def list_contacts(
        self,
        *,
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[ContactListItem], str | None]:
        docs, next_cursor = await self.pings_repo.list_contacts(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )
        items = [await self._to_contact_item(doc, user_id=user_id) for doc in docs]
        return items, next_cursor

    async def get_contact_extras(
        self, *, viewer_user_id: str, peer_user_id: str
    ) -> ContactExtras:
        """Aggregate extra data for an accepted contact.

        Returns empty extras (never raises) when the pair is not an accepted
        contact, so callers like the users endpoint can request it opportunistically.
        """
        if viewer_user_id == peer_user_id:
            return ContactExtras()

        ping = await self.pings_repo.get_pair_state(
            user_a=viewer_user_id, user_b=peer_user_id
        )
        if ping is None or self._doc_value(ping, "status") != "accepted":
            return ContactExtras()

        conversation_id = None
        if self.conversations_repo is not None:
            from app.modules.conversations.repository.helpers import dm_key_for

            conversation = await self.conversations_repo.get_by_dm_key(
                dm_key_for(viewer_user_id, peer_user_id)
            )
            conversation_id = conversation.str_id if conversation is not None else None

        return ContactExtras(
            connection_timestamp=(
                self._doc_value(ping, "responded_at")
                or self._doc_value(ping, "updated_at")
            ),
            conversation_id=conversation_id,
            shared_conversations=await self._shared_conversations(
                user_id=viewer_user_id, peer_user_id=peer_user_id
            ),
            shared_spaces=await self._shared_spaces(
                user_id=viewer_user_id, peer_user_id=peer_user_id
            ),
        )

    async def shares_context(self, viewer_user_id: str, peer_user_id: str) -> bool:
        if viewer_user_id == peer_user_id:
            return True
        shared_convs = await self._shared_conversations(
            user_id=viewer_user_id, peer_user_id=peer_user_id
        )
        if shared_convs:
            return True
        shared_spaces = await self._shared_spaces(
            user_id=viewer_user_id, peer_user_id=peer_user_id
        )
        if shared_spaces:
            return True
        return False

    async def _shared_conversations(
        self, *, user_id: str, peer_user_id: str
    ) -> list[SharedConversationSummary]:
        from app.db.models import ConversationDocument, ParticipantDocument
        from app.db.object_id import parse_object_id

        my_parts = await ParticipantDocument.find(
            {"user_id": str(user_id), "hidden": {"$ne": True}}
        ).to_list()
        my_conv_ids = {str(part.conversation_id) for part in my_parts}
        if not my_conv_ids:
            return []

        peer_parts = await ParticipantDocument.find(
            {
                "user_id": str(peer_user_id),
                "conversation_id": {"$in": list(my_conv_ids)},
                "hidden": {"$ne": True},
            }
        ).to_list()
        shared_ids = [str(part.conversation_id) for part in peer_parts]
        if not shared_ids:
            return []

        conversations = await ConversationDocument.find(
            {
                "_id": {"$in": [parse_object_id(cid) for cid in shared_ids]},
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
        from app.db.models import SpaceDocument, SpaceMemberDocument
        from app.db.object_id import parse_object_id

        my_members = await SpaceMemberDocument.find(
            {"user_id": str(user_id)}
        ).to_list()
        my_space_ids = {str(member.space_id) for member in my_members}
        if not my_space_ids:
            return []

        peer_members = await SpaceMemberDocument.find(
            {
                "user_id": str(peer_user_id),
                "space_id": {"$in": list(my_space_ids)},
            }
        ).to_list()
        shared_ids = [str(member.space_id) for member in peer_members]
        if not shared_ids:
            return []

        spaces = await SpaceDocument.find(
            {"_id": {"$in": [parse_object_id(sid) for sid in shared_ids]}}
        ).to_list()
        return [
            SharedSpaceSummary(id=space.str_id, name=space.name, slug=space.slug)
            for space in spaces
        ]

    def _to_ping_response(self, doc: PingDocument) -> PingResponse:
        return PingResponse(
            id=self._doc_value(doc, "id", ""),
            from_user_id=self._doc_value(doc, "from_user_id"),
            to_user_id=self._doc_value(doc, "to_user_id"),
            status=self._doc_value(doc, "status"),
            created_at=self._doc_value(doc, "created_at"),
            updated_at=self._doc_value(doc, "updated_at"),
            responded_at=self._doc_value(doc, "responded_at"),
        )

    async def _to_list_item(
        self, doc: PingDocument, *, user_id: str, incoming: bool
    ) -> PingListItem:
        peer_id = (
            self._doc_value(doc, "from_user_id")
            if incoming
            else self._doc_value(doc, "to_user_id")
        )
        peer = await self.users_repo.find_by_id(peer_id)
        online = (
            await self.presence_service.is_online(peer_id)
            if self.presence_service
            else False
        )

        peer_summary = PeerUserSummary(
            id=peer_id,
            username=self._doc_value(peer, "username", "") if peer else "",
            display_name=self._doc_value(peer, "display_name") if peer else None,
            avatar=(
                build_user_avatar_payload(self._doc_value(peer, "avatar"))
                if peer
                else None
            ),
            is_online=online,
        )
        return PingListItem(
            ping=self._to_ping_response(doc),
            peer=peer_summary,
        )

    async def to_realtime_payload(
        self, doc: PingDocument, *, incoming_for: str
    ) -> dict[str, Any]:
        peer_id = (
            self._doc_value(doc, "from_user_id")
            if self._doc_value(doc, "to_user_id") == incoming_for
            else self._doc_value(doc, "to_user_id")
        )
        peer = await self.users_repo.find_by_id(peer_id)
        is_online = (
            await self.presence_service.is_online(peer_id)
            if self.presence_service
            else False
        )

        return {
            "ping": {
                "id": self._doc_value(doc, "id", ""),
                "from_user_id": self._doc_value(doc, "from_user_id"),
                "to_user_id": self._doc_value(doc, "to_user_id"),
                "status": self._doc_value(doc, "status"),
                "created_at": self._doc_value(doc, "created_at").isoformat(),
                "updated_at": self._doc_value(doc, "updated_at").isoformat(),
                "responded_at": (
                    self._doc_value(doc, "responded_at").isoformat()
                    if self._doc_value(doc, "responded_at")
                    else None
                ),
            },
            "peer": {
                "id": peer_id,
                "username": self._doc_value(peer, "username", "") if peer else "",
                "display_name": self._doc_value(peer, "display_name") if peer else None,
                "avatar": (
                    build_user_avatar_payload(self._doc_value(peer, "avatar"))
                    if peer
                    else None
                ),
                "is_online": is_online,
            },
        }

    async def get_contact_state(
        self, *, viewer_user_id: str, peer_user_id: str
    ) -> ContactState:
        if viewer_user_id == peer_user_id:
            return ContactState(
                can_ping=False,
                chat_allowed=False,
                ping_status="none",
            )

        doc = await self.pings_repo.get_pair_state(
            user_a=viewer_user_id, user_b=peer_user_id
        )
        blocked_by_me, blocks_me = await self.pings_repo.get_block_state(
            viewer_user_id=viewer_user_id,
            peer_user_id=peer_user_id,
        )
        return self._contact_state_from_doc(
            viewer_user_id=viewer_user_id,
            doc=doc,
            blocked_by_me=blocked_by_me,
            blocks_me=blocks_me,
        )

    async def get_contact_states(
        self, *, viewer_user_id: str, peer_user_ids: list[str]
    ) -> dict[str, ContactState]:
        if not peer_user_ids:
            return {}

        unique_peer_ids = list(dict.fromkeys(peer_user_ids))
        docs_by_pair_id = await self.pings_repo.get_pair_states(
            user_id=viewer_user_id,
            peer_user_ids=unique_peer_ids,
        )

        states: dict[str, ContactState] = {}
        for peer_user_id in unique_peer_ids:
            blocked_by_me, blocks_me = await self.pings_repo.get_block_state(
                viewer_user_id=viewer_user_id,
                peer_user_id=peer_user_id,
            )
            states[peer_user_id] = self._contact_state_from_doc(
                viewer_user_id=viewer_user_id,
                doc=docs_by_pair_id.get(pair_id_for(viewer_user_id, peer_user_id)),
                blocked_by_me=blocked_by_me,
                blocks_me=blocks_me,
            )
        return states

    def _contact_state_from_doc(
        self,
        *,
        viewer_user_id: str,
        doc: PingDocument | None,
        blocked_by_me: bool = False,
        blocks_me: bool = False,
    ) -> ContactState:
        if blocked_by_me or blocks_me:
            return ContactState(
                can_ping=False,
                chat_allowed=False,
                ping_status="blocked",
                blocked_by_me=blocked_by_me,
                blocks_me=blocks_me,
            )

        if not doc:
            return ContactState(
                can_ping=True,
                chat_allowed=False,
                ping_status="none",
            )

        status = self._doc_value(doc, "status")

        if status == "accepted":
            return ContactState(
                can_ping=False,
                chat_allowed=True,
                ping_status="accepted",
            )

        if status == "pending":
            if self._doc_value(doc, "to_user_id") == viewer_user_id:
                return ContactState(
                    can_ping=False,
                    chat_allowed=False,
                    ping_status="incoming_pending",
                )
            return ContactState(
                can_ping=False,
                chat_allowed=False,
                ping_status="outgoing_pending",
            )

        if status == "declined":
            return ContactState(
                can_ping=True,
                chat_allowed=False,
                ping_status="declined",
            )

        return ContactState(
            can_ping=True,
            chat_allowed=False,
            ping_status="none",
        )

    async def _to_contact_item(
        self, doc: PingDocument, *, user_id: str
    ) -> ContactListItem:
        peer_id = (
            self._doc_value(doc, "to_user_id")
            if self._doc_value(doc, "from_user_id") == user_id
            else self._doc_value(doc, "from_user_id")
        )
        peer = await self.users_repo.find_by_id(peer_id)
        online = (
            await self.presence_service.is_online(peer_id)
            if self.presence_service
            else False
        )
        conversation_id = None
        if self.conversations_repo is not None:
            from app.modules.conversations.repository.helpers import dm_key_for

            conversation = await self.conversations_repo.get_by_dm_key(
                dm_key_for(user_id, peer_id)
            )
            conversation_id = conversation.str_id if conversation is not None else None

        return ContactListItem(
            ping=self._to_ping_response(doc),
            peer=PeerUserSummary(
                id=peer_id,
                username=self._doc_value(peer, "username", "") if peer else "",
                display_name=self._doc_value(peer, "display_name") if peer else None,
                avatar=(
                    build_user_avatar_payload(self._doc_value(peer, "avatar"))
                    if peer
                    else None
                ),
                is_online=online,
            ),
            conversation_id=conversation_id,
        )
