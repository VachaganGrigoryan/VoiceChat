from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.db.models import RelationshipDocument
from app.db.models.relationship import (
    RelationshipInitiation,
    RelationshipKind,
    RelationshipStatus,
    RelationshipTargetType,
)
from app.db.object_id import parse_object_id as _oid
from app.db.repository import BaseRepository


def pair_id_for(user_a: str, user_b: str) -> str:
    """Canonical, order-independent id for a connection's user pair."""
    a = str(user_a)
    b = str(user_b)
    return f"{a}_{b}" if a < b else f"{b}_{a}"


class RelationshipsRepository(BaseRepository[RelationshipDocument]):
    model = RelationshipDocument

    async def create(
        self,
        *,
        kind: RelationshipKind,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        status: RelationshipStatus,
        initiation: RelationshipInitiation,
        initiated_by: str,
        approved_by: str | None = None,
        pair_id: str | None = None,
        role_ids: list[str] | None = None,
        state: dict[str, Any] | None = None,
    ) -> RelationshipDocument:
        now = datetime.now(UTC)
        doc = RelationshipDocument(
            kind=kind,
            user_id=str(user_id),
            target_type=target_type,
            target_id=str(target_id),
            status=status,
            initiation=initiation,
            initiated_by=str(initiated_by),
            approved_by=str(approved_by) if approved_by else None,
            requested_at=now,
            activated_at=now if status == "active" else None,
            pair_id=pair_id,
            role_ids=[str(role_id) for role_id in (role_ids or [])],
            created_at=now,
            updated_at=now,
        )
        if state:
            doc.state = doc.state.model_copy(update=state)
        try:
            await doc.insert()
        except DuplicateKeyError as exc:
            raise AppError(
                code="RELATIONSHIP_ALREADY_EXISTS",
                message="Relationship already exists",
                status_code=409,
            ) from exc
        return doc

    async def find_by_id(self, relationship_id: str) -> RelationshipDocument | None:
        return await self.get_by_id(relationship_id)

    async def find_edge(
        self,
        *,
        kind: RelationshipKind,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
    ) -> RelationshipDocument | None:
        return await RelationshipDocument.find_one(
            {
                "kind": kind,
                "user_id": str(user_id),
                "target_type": target_type,
                "target_id": str(target_id),
            }
        )

    async def find_connection(self, *, user_a: str, user_b: str) -> RelationshipDocument | None:
        return await RelationshipDocument.find_one(
            {"kind": "connection", "pair_id": pair_id_for(user_a, user_b)}
        )

    async def find_connections(
        self, *, user_id: str, peer_user_ids: list[str]
    ) -> dict[str, RelationshipDocument]:
        """Active-or-not connection docs for ``user_id`` keyed by ``pair_id``."""
        if not peer_user_ids:
            return {}
        pair_ids = [
            pair_id_for(user_id, peer_user_id)
            for peer_user_id in dict.fromkeys(peer_user_ids)
        ]
        docs = await RelationshipDocument.find(
            {"kind": "connection", "pair_id": {"$in": pair_ids}}
        ).to_list()
        return {doc.pair_id: doc for doc in docs if doc.pair_id}

    async def list_for_user(
        self,
        *,
        kind: RelationshipKind | None = None,
        user_id: str,
        target_type: RelationshipTargetType | None = None,
        status: RelationshipStatus | None = None,
        limit: int = 100,
    ) -> list[RelationshipDocument]:
        query: dict[str, Any] = {"user_id": str(user_id)}
        if kind is not None:
            query["kind"] = kind
        if target_type is not None:
            query["target_type"] = target_type
        if status is not None:
            query["status"] = status
        return (
            await RelationshipDocument.find(query)
            .sort("-updated_at", "-_id")
            .limit(limit)
            .to_list()
        )

    async def list_for_target(
        self,
        *,
        kind: RelationshipKind,
        target_type: RelationshipTargetType,
        target_id: str,
        status: RelationshipStatus | None = None,
        limit: int = 500,
    ) -> list[RelationshipDocument]:
        query: dict[str, Any] = {
            "kind": kind,
            "target_type": target_type,
            "target_id": str(target_id),
        }
        if status is not None:
            query["status"] = status
        return (
            await RelationshipDocument.find(query)
            .sort("-updated_at", "-_id")
            .limit(limit)
            .to_list()
        )

    async def set_status(
        self,
        *,
        relationship_id: str,
        status: RelationshipStatus,
        approved_by: str | None = None,
        expected_status: RelationshipStatus | None = None,
    ) -> RelationshipDocument | None:
        """Atomically transition a relationship, stamping lifecycle timestamps.

        ``expected_status`` makes the write a CAS so concurrent accept/decline
        calls cannot both win.
        """
        now = datetime.now(UTC)
        updates: dict[str, Any] = {"status": status, "updated_at": now}
        if status == "active":
            updates["activated_at"] = now
            updates["ended_at"] = None
            if approved_by is not None:
                updates["approved_by"] = str(approved_by)
        elif status in {"declined", "revoked"}:
            updates["ended_at"] = now

        query: dict[str, Any] = {"_id": _oid(relationship_id)}
        if expected_status is not None:
            query["status"] = expected_status
        return await self.find_one_and_update(query, {"$set": updates})

    async def reopen(
        self,
        *,
        relationship_id: str,
        user_id: str,
        initiated_by: str,
        initiation: RelationshipInitiation,
        status: RelationshipStatus,
    ) -> RelationshipDocument | None:
        """Re-request a previously declined/revoked edge, reusing its document.

        Reuse (rather than insert) keeps the per-kind unique indexes satisfied.
        """
        now = datetime.now(UTC)
        return await self.find_one_and_update(
            {
                "_id": _oid(relationship_id),
                "status": {"$in": ["declined", "revoked"]},
            },
            {
                "$set": {
                    "user_id": str(user_id),
                    "status": status,
                    "initiation": initiation,
                    "initiated_by": str(initiated_by),
                    "approved_by": None,
                    "requested_at": now,
                    "activated_at": now if status == "active" else None,
                    "ended_at": None,
                    "updated_at": now,
                }
            },
        )

    async def update_state(
        self, *, relationship_id: str, updates: dict[str, Any]
    ) -> RelationshipDocument | None:
        """Apply a dotted ``state.*`` patch to one relationship."""
        if not updates:
            return await self.find_by_id(relationship_id)
        set_fields = {f"state.{key}": value for key, value in updates.items()}
        set_fields["updated_at"] = datetime.now(UTC)
        return await self.find_one_and_update(
            {"_id": _oid(relationship_id)}, {"$set": set_fields}
        )

    async def update_state_for_edge(
        self,
        *,
        kind: RelationshipKind,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        updates: dict[str, Any],
    ) -> RelationshipDocument | None:
        if not updates:
            return None
        set_fields = {f"state.{key}": value for key, value in updates.items()}
        set_fields["updated_at"] = datetime.now(UTC)
        return await self.find_one_and_update(
            {
                "kind": kind,
                "user_id": str(user_id),
                "target_type": target_type,
                "target_id": str(target_id),
            },
            {"$set": set_fields},
        )

    async def upsert_membership(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        role_ids: list[str] | None = None,
        initiated_by: str | None = None,
        initiation: RelationshipInitiation = "direct",
    ) -> RelationshipDocument:
        """Ensure an active membership exists (idempotent add-participant path)."""
        now = datetime.now(UTC)
        set_on_insert: dict[str, Any] = {
            "kind": "membership",
            "user_id": str(user_id),
            "target_type": target_type,
            "target_id": str(target_id),
            "initiation": initiation,
            "initiated_by": str(initiated_by or user_id),
            "requested_at": now,
            "created_at": now,
            "state": {},
        }
        updates: dict[str, Any] = {
            "status": "active",
            "activated_at": now,
            "ended_at": None,
            "updated_at": now,
        }
        if role_ids is not None:
            updates["role_ids"] = [str(role_id) for role_id in role_ids]

        try:
            raw = await self.raw.find_one_and_update(
                {
                    "kind": "membership",
                    "user_id": str(user_id),
                    "target_type": target_type,
                    "target_id": str(target_id),
                },
                {"$set": updates, "$setOnInsert": set_on_insert},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:  # pragma: no cover - index guarantees a winner
            raw = await self.raw.find_one(
                {
                    "kind": "membership",
                    "user_id": str(user_id),
                    "target_type": target_type,
                    "target_id": str(target_id),
                }
            )
        return RelationshipDocument.model_validate(raw)

    async def delete_edge(
        self,
        *,
        kind: RelationshipKind,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
    ) -> bool:
        result = await self.raw.delete_one(
            {
                "kind": kind,
                "user_id": str(user_id),
                "target_type": target_type,
                "target_id": str(target_id),
            }
        )
        return result.deleted_count > 0

    async def count_for_target(
        self,
        *,
        kind: RelationshipKind,
        target_type: RelationshipTargetType,
        target_id: str,
        status: RelationshipStatus | None = None,
    ) -> int:
        query: dict[str, Any] = {
            "kind": kind,
            "target_type": target_type,
            "target_id": str(target_id),
        }
        if status is not None:
            query["status"] = status
        return await RelationshipDocument.find(query).count()
