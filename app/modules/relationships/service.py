from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.db.models import RelationshipDocument
from app.db.models.relationship import (
    RelationshipInitiation,
    RelationshipKind,
    RelationshipStatus,
    RelationshipTargetType,
)
from app.modules.relationships.repository import RelationshipsRepository, pair_id_for

# §93 invariants: which targets each kind may point at.
ALLOWED_TARGET_TYPES: dict[RelationshipKind, set[RelationshipTargetType]] = {
    "connection": {"user"},
    "follow": {"user", "channel"},
    "membership": {"conversation", "channel", "space"},
}

_REOPENABLE = {"declined", "revoked"}


class RelationshipService:
    """The single mutator for every relationship kind (design §2).

    `ConnectionService`, `FollowService`, and `MembershipService` are thin
    facades over this engine; they choose `kind`/defaults and apply domain
    rules, but all state transitions land here.
    """

    def __init__(self, *, repo: RelationshipsRepository | None = None) -> None:
        self.repo = repo or RelationshipsRepository()

    def validate(
        self,
        *,
        kind: RelationshipKind,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        pair_id: str | None = None,
    ) -> None:
        allowed = ALLOWED_TARGET_TYPES[kind]
        if target_type not in allowed:
            raise AppError(
                code="RELATIONSHIP_INVALID_TARGET",
                message=(
                    f"{kind} relationships target {'/'.join(sorted(allowed))}, "
                    f"not {target_type}"
                ),
                status_code=400,
            )
        if kind == "connection" and not pair_id:
            raise AppError(
                code="RELATIONSHIP_MISSING_PAIR_ID",
                message="Connection relationships require a pair_id",
                status_code=400,
            )
        if target_type == "user" and str(user_id) == str(target_id):
            raise AppError(
                code="RELATIONSHIP_SELF_TARGET",
                message="Cannot create a relationship with yourself",
                status_code=400,
            )

    async def _open(
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
        """Create the edge, or revive a previously declined/revoked one.

        The per-kind unique indexes mean a closed edge still occupies the slot,
        so re-requesting reuses that document rather than inserting a second.
        """
        self.validate(
            kind=kind,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            pair_id=pair_id,
        )

        existing = (
            await self.repo.find_connection(user_a=user_id, user_b=target_id)
            if kind == "connection"
            else await self.repo.find_edge(
                kind=kind,
                user_id=user_id,
                target_type=target_type,
                target_id=target_id,
            )
        )
        if existing is not None:
            if existing.status in _REOPENABLE:
                reopened = await self.repo.reopen(
                    relationship_id=existing.str_id,
                    user_id=user_id,
                    initiated_by=initiated_by,
                    initiation=initiation,
                    status=status,
                )
                if reopened is not None:
                    return reopened
                existing = await self.repo.find_by_id(existing.str_id)
                if existing is None:  # pragma: no cover - deleted mid-flight
                    raise AppError(
                        code="RELATIONSHIP_NOT_FOUND",
                        message="Relationship not found",
                        status_code=404,
                    )
            if existing.status == "active":
                raise AppError(
                    code="RELATIONSHIP_ALREADY_ACTIVE",
                    message="Relationship is already active",
                    status_code=409,
                )
            return existing

        return await self.repo.create(
            kind=kind,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            status=status,
            initiation=initiation,
            initiated_by=initiated_by,
            approved_by=approved_by,
            pair_id=pair_id,
            role_ids=role_ids,
            state=state,
        )

    async def request(
        self,
        *,
        kind: RelationshipKind,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        status: RelationshipStatus = "pending",
        role_ids: list[str] | None = None,
        state: dict[str, Any] | None = None,
    ) -> RelationshipDocument:
        """The subject asks for the relationship (`initiation = request`)."""
        return await self._open(
            kind=kind,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            status=status,
            initiation="request",
            initiated_by=user_id,
            pair_id=pair_id_for(user_id, target_id) if kind == "connection" else None,
            role_ids=role_ids,
            state=state,
        )

    async def invite(
        self,
        *,
        kind: RelationshipKind,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        invited_by: str,
        status: RelationshipStatus = "pending",
        role_ids: list[str] | None = None,
        state: dict[str, Any] | None = None,
    ) -> RelationshipDocument:
        """An authority offers the relationship to ``user_id``."""
        return await self._open(
            kind=kind,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            status=status,
            initiation="invite",
            initiated_by=invited_by,
            pair_id=pair_id_for(user_id, target_id) if kind == "connection" else None,
            role_ids=role_ids,
            state=state,
        )

    async def _require(self, relationship_id: str) -> RelationshipDocument:
        doc = await self.repo.find_by_id(relationship_id)
        if doc is None:
            raise AppError(
                code="RELATIONSHIP_NOT_FOUND",
                message="Relationship not found",
                status_code=404,
            )
        return doc

    async def accept(
        self, *, relationship_id: str, approved_by: str
    ) -> RelationshipDocument:
        doc = await self._require(relationship_id)
        if doc.status != "pending":
            raise AppError(
                code="RELATIONSHIP_NOT_PENDING",
                message="Relationship is not pending",
                status_code=409,
            )
        updated = await self.repo.set_status(
            relationship_id=relationship_id,
            status="active",
            approved_by=approved_by,
            expected_status="pending",
        )
        if updated is None:
            raise AppError(
                code="RELATIONSHIP_NOT_PENDING",
                message="Relationship is not pending",
                status_code=409,
            )
        return updated

    async def decline(
        self, *, relationship_id: str, declined_by: str | None = None
    ) -> RelationshipDocument:
        doc = await self._require(relationship_id)
        if doc.status != "pending":
            raise AppError(
                code="RELATIONSHIP_NOT_PENDING",
                message="Relationship is not pending",
                status_code=409,
            )
        updated = await self.repo.set_status(
            relationship_id=relationship_id,
            status="declined",
            expected_status="pending",
        )
        if updated is None:
            raise AppError(
                code="RELATIONSHIP_NOT_PENDING",
                message="Relationship is not pending",
                status_code=409,
            )
        return updated

    async def activate(
        self, *, relationship_id: str, approved_by: str | None = None
    ) -> RelationshipDocument:
        """Force a relationship active without requiring a pending predecessor."""
        await self._require(relationship_id)
        updated = await self.repo.set_status(
            relationship_id=relationship_id,
            status="active",
            approved_by=approved_by,
        )
        if updated is None:  # pragma: no cover - deleted mid-flight
            raise AppError(
                code="RELATIONSHIP_NOT_FOUND",
                message="Relationship not found",
                status_code=404,
            )
        return updated

    async def revoke(self, *, relationship_id: str) -> RelationshipDocument:
        doc = await self._require(relationship_id)
        if doc.status == "revoked":
            return doc
        updated = await self.repo.set_status(
            relationship_id=relationship_id, status="revoked"
        )
        if updated is None:  # pragma: no cover - deleted mid-flight
            raise AppError(
                code="RELATIONSHIP_NOT_FOUND",
                message="Relationship not found",
                status_code=404,
            )
        return updated

    async def list(
        self,
        *,
        kind: RelationshipKind | None = None,
        user_id: str | None = None,
        target_type: RelationshipTargetType | None = None,
        target_id: str | None = None,
        status: RelationshipStatus | None = None,
        limit: int = 100,
    ) -> list[RelationshipDocument]:
        """List a user's edges, or a target's edges when ``target_id`` is given."""
        if user_id is not None:
            return await self.repo.list_for_user(
                kind=kind,
                user_id=user_id,
                target_type=target_type,
                status=status,
                limit=limit,
            )
        if kind is None or target_type is None or target_id is None:
            raise AppError(
                code="RELATIONSHIP_QUERY_INVALID",
                message="Provide user_id, or kind + target_type + target_id",
                status_code=400,
            )
        return await self.repo.list_for_target(
            kind=kind,
            target_type=target_type,
            target_id=target_id,
            status=status,
            limit=limit,
        )
