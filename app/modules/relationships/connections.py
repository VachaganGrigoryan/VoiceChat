from __future__ import annotations

from app.core.errors import AppError
from app.db.models import BlockDocument, RelationshipDocument
from app.modules.relationships.repository import RelationshipsRepository, pair_id_for
from app.modules.relationships.service import RelationshipService


class ConnectionService:
    """Mutual user↔user contact — the Ping/Pong product concept (design §3).

    One document per canonical user pair; accepting makes the edge symmetric.
    Replaces `PingsService.ensure_can_message` as the DM permission gate.
    """

    def __init__(
        self,
        *,
        engine: RelationshipService | None = None,
        repo: RelationshipsRepository | None = None,
    ) -> None:
        self.repo = repo or RelationshipsRepository()
        self.engine = engine or RelationshipService(repo=self.repo)

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
        if await self.is_blocked(user_a=from_user_id, user_b=to_user_id):
            raise AppError(
                code="CONNECTION_BLOCKED",
                message="Cannot connect with this user",
                status_code=403,
            )
        return await self.engine.request(
            kind="connection",
            user_id=from_user_id,
            target_type="user",
            target_id=to_user_id,
        )

    async def accept(self, *, user_id: str, relationship_id: str) -> RelationshipDocument:
        doc = await self._require_counterparty(
            user_id=user_id, relationship_id=relationship_id
        )
        return await self.engine.accept(
            relationship_id=doc.str_id, approved_by=user_id
        )

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
        self, *, user_id: str, status: str = "active", limit: int = 100
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

    def peer_of(self, doc: RelationshipDocument, *, user_id: str) -> str:
        return (
            str(doc.target_id)
            if str(doc.user_id) == str(user_id)
            else str(doc.user_id)
        )

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
