from __future__ import annotations

from typing import Any, Protocol

from fastapi import HTTPException

from app.core.errors import AppError
from app.modules.pings.repository import PingsRepository, pair_id_for
from app.modules.pings.schemas import PingListItem, PingResponse, PeerUserSummary, ContactState


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> dict[str, Any] | None: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


class PingsService:
    def __init__(
        self,
        *,
        pings_repo: PingsRepository,
        users_repo: UsersRepositoryProto,
        presence_service: PresenceServiceProto | None = None,
    ) -> None:
        self.pings_repo = pings_repo
        self.users_repo = users_repo
        self.presence_service = presence_service

    async def send_ping(self, *, from_user_id: str, to_user_id: str) -> PingResponse:
        if from_user_id == to_user_id:
            raise HTTPException(status_code=400, detail="Cannot ping yourself")

        target = await self.users_repo.find_by_id(to_user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        existing = await self.pings_repo.find_by_pair_id(pair_id_for(from_user_id, to_user_id))
        if existing:
            if existing["status"] == "accepted":
                raise HTTPException(status_code=409, detail="Chat permission already granted")
            if existing["status"] == "pending":
                return self._to_ping_response(existing)
                # raise HTTPException(status_code=409, detail="Ping already pending")

        doc = await self.pings_repo.create_ping(from_user_id=from_user_id, to_user_id=to_user_id)
        return self._to_ping_response(doc)

    async def accept_ping(self, *, user_id: str, ping_id: str) -> PingResponse:
        ping = await self.pings_repo.find_by_id(ping_id)
        if not ping:
            raise HTTPException(status_code=404, detail="Ping not found")
        if ping["to_user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not allowed to accept this ping")
        if ping["status"] != "pending":
            raise HTTPException(status_code=409, detail="Ping is not pending")

        updated = await self.pings_repo.update_status(ping_id=ping_id, status="accepted")
        assert updated is not None
        return self._to_ping_response(updated)

    async def decline_ping(self, *, user_id: str, ping_id: str) -> PingResponse:
        ping = await self.pings_repo.find_by_id(ping_id)
        if not ping:
            raise HTTPException(status_code=404, detail="Ping not found")
        if ping["to_user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not allowed to decline this ping")
        if ping["status"] != "pending":
            raise HTTPException(status_code=409, detail="Ping is not pending")

        updated = await self.pings_repo.update_status(ping_id=ping_id, status="declined")
        assert updated is not None
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
        items = [await self._to_list_item(doc, user_id=user_id, incoming=True) for doc in docs]
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
        items = [await self._to_list_item(doc, user_id=user_id, incoming=False) for doc in docs]
        return items, next_cursor

    async def has_chat_permission(self, *, user_a: str, user_b: str) -> bool:
        return await self.pings_repo.has_accepted_permission(user_a=user_a, user_b=user_b)

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
        if ping["from_user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not allowed to cancel this ping")
        if ping["status"] != "pending":
            raise HTTPException(status_code=409, detail="Ping is not pending")

        updated = await self.pings_repo.update_status(ping_id=ping_id, status="cancelled")
        assert updated is not None
        return self._to_ping_response(updated)

    async def block_user(self, *, user_id: str, peer_user_id: str):
        doc = await self.pings_repo.block_pair(
            user_a=user_id,
            user_b=peer_user_id,
            by_user_id=user_id,
        )
        return self._to_ping_response(doc)

    async def unblock_user(self, *, user_id: str, peer_user_id: str):
        doc = await self.pings_repo.unblock_pair(
            user_a=user_id,
            user_b=peer_user_id,
            by_user_id=user_id,
        )
        return self._to_ping_response(doc)

    async def list_blocked(self, *, user_id: str):
        docs = await self.pings_repo.list_blocked(user_id=user_id)
        return [self._to_ping_response(doc) for doc in docs]

    def _to_ping_response(self, doc: dict[str, Any]) -> PingResponse:
        return PingResponse(
            id=str(doc["_id"]),
            from_user_id=doc["from_user_id"],
            to_user_id=doc["to_user_id"],
            status=doc["status"],
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            responded_at=doc.get("responded_at"),
        )

    async def _to_list_item(self, doc: dict[str, Any], *, user_id: str, incoming: bool) -> PingListItem:
        peer_id = doc["from_user_id"] if incoming else doc["to_user_id"]
        peer = await self.users_repo.find_by_id(peer_id)
        online = await self.presence_service.is_online(peer_id) if self.presence_service else False

        peer_summary = PeerUserSummary(
            id=peer_id,
            username=peer.get("username", "") if peer else "",
            display_name=peer.get("display_name") if peer else None,
            avatar=peer.get("avatar") if peer else None,
            is_online=online,
        )
        return PingListItem(
            ping=self._to_ping_response(doc),
            peer=peer_summary,
        )

    async def to_realtime_payload(self, doc: dict[str, Any], *, incoming_for: str) -> dict[str, Any]:
        peer_id = doc["from_user_id"] if doc["to_user_id"] == incoming_for else doc["to_user_id"]
        peer = await self.users_repo.find_by_id(peer_id)
        is_online = await self.presence_service.is_online(peer_id) if self.presence_service else False

        return {
            "ping": {
                "id": str(doc["_id"]),
                "from_user_id": doc["from_user_id"],
                "to_user_id": doc["to_user_id"],
                "status": doc["status"],
                "created_at": doc["created_at"].isoformat(),
                "updated_at": doc["updated_at"].isoformat(),
                "responded_at": doc["responded_at"].isoformat() if doc.get("responded_at") else None,
            },
            "peer": {
                "id": peer_id,
                "username": peer.get("username", "") if peer else "",
                "display_name": peer.get("display_name") if peer else None,
                "avatar": peer.get("avatar") if peer else None,
                "is_online": is_online,
            },
        }

    async def get_contact_state(self, *, viewer_user_id: str, peer_user_id: str) -> ContactState:
        if viewer_user_id == peer_user_id:
            return ContactState(
                can_ping=False,
                chat_allowed=False,
                ping_status="none",
            )

        doc = await self.pings_repo.get_pair_state(user_a=viewer_user_id, user_b=peer_user_id)
        return self._contact_state_from_doc(viewer_user_id=viewer_user_id, doc=doc)

    async def get_contact_states(self, *, viewer_user_id: str, peer_user_ids: list[str]) -> dict[str, ContactState]:
        if not peer_user_ids:
            return {}

        unique_peer_ids = list(dict.fromkeys(peer_user_ids))
        docs_by_pair_id = await self.pings_repo.get_pair_states(
            user_id=viewer_user_id,
            peer_user_ids=unique_peer_ids,
        )

        return {
            peer_user_id: self._contact_state_from_doc(
                viewer_user_id=viewer_user_id,
                doc=docs_by_pair_id.get(pair_id_for(viewer_user_id, peer_user_id)),
            )
            for peer_user_id in unique_peer_ids
        }

    def _contact_state_from_doc(self, *, viewer_user_id: str, doc: dict[str, Any] | None) -> ContactState:
        if not doc:
            return ContactState(
                can_ping=True,
                chat_allowed=False,
                ping_status="none",
            )

        status = doc["status"]

        if status == "accepted":
            return ContactState(
                can_ping=False,
                chat_allowed=True,
                ping_status="accepted",
            )

        if status == "pending":
            if doc["to_user_id"] == viewer_user_id:
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
