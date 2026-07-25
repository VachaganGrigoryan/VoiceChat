from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.models import (
    SpaceDocument,
    SpaceMemberDocument,
    InviteLinkDocument,
    JoinRequestDocument,
    RelationshipDocument,
)
from app.db.object_id import parse_object_id
from app.db.repository import BaseRepository
from app.modules.relationships.compat import (
    RELATIONSHIP_STATUS_BY_REQUEST_STATUS as _RELATIONSHIP_STATUS_BY_REQUEST_STATUS,
    membership_filter,
    to_join_request,
    to_space_member,
)
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.service import RelationshipService


def _space_membership_filter(space_id: str, user_id: str) -> dict:
    return membership_filter(
        target_type="space", target_id=space_id, user_id=user_id
    )

async def find_active_space_membership(
    *, space_id: str, user_id: str
) -> SpaceMemberDocument | None:
    """Module-level lookup for callers outside `SpacesRepository`."""
    doc = await RelationshipDocument.find_one(
        {**_space_membership_filter(space_id, user_id), "status": "active"}
    )
    return to_space_member(doc) if doc is not None else None


class SpacesRepository(BaseRepository[SpaceDocument]):
    model = SpaceDocument

    async def get_by_id(
        self, id: str, *, invalid_message: str = "Document not found"
    ) -> SpaceDocument | None:
        try:
            return await SpaceDocument.get(parse_object_id(id))
        except Exception:
            return None

    async def get_by_slug(self, slug: str) -> SpaceDocument | None:
        return await SpaceDocument.find_one({"slug": slug})

    async def ensure_membership(
        self, *, space_id: str, user_id: str, role: Literal["owner", "admin", "member"]
    ) -> SpaceMemberDocument:
        now = datetime.now(UTC)
        collection = RelationshipDocument.get_pymongo_collection()
        try:
            raw = await collection.find_one_and_update(
                _space_membership_filter(space_id, user_id),
                {
                    "$set": {
                        "role_ids": [role],
                        "status": "active",
                        "activated_at": now,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "kind": "membership",
                        "target_type": "space",
                        "target_id": str(space_id),
                        "user_id": str(user_id),
                        "initiation": "direct",
                        "initiated_by": str(user_id),
                        "requested_at": now,
                        "ended_at": None,
                        "state": {},
                        "created_at": now,
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:  # pragma: no cover - index guarantees a winner
            raw = await collection.find_one(
                _space_membership_filter(space_id, user_id)
            )
        return to_space_member(RelationshipDocument.model_validate(raw))

    async def get_membership(self, *, space_id: str, user_id: str) -> SpaceMemberDocument | None:
        return await find_active_space_membership(space_id=space_id, user_id=user_id)

    async def list_memberships_for_user(self, *, user_id: str) -> list[SpaceMemberDocument]:
        docs = await RelationshipDocument.find(
            {
                "kind": "membership",
                "target_type": "space",
                "user_id": str(user_id),
                "status": "active",
            }
        ).to_list()
        return [to_space_member(doc) for doc in docs]

    async def list_members_for_space(self, *, space_id: str) -> list[SpaceMemberDocument]:
        docs = await RelationshipDocument.find(
            {
                "kind": "membership",
                "target_type": "space",
                "target_id": str(space_id),
                "status": "active",
            }
        ).to_list()
        return [to_space_member(doc) for doc in docs]

    async def create_invite_link(
        self,
        *,
        target_id: str,
        created_by: str,
        code: str,
        expires_at: datetime | None,
        max_uses: int | None,
        requires_approval: bool,
        invitee_id: str | None = None,
    ) -> InviteLinkDocument:
        now = datetime.now(UTC)
        invite = InviteLinkDocument(
            target_type="space",
            target_id=str(target_id),
            code=code,
            created_by=str(created_by),
            expires_at=expires_at,
            max_uses=max_uses,
            requires_approval=requires_approval,
            invitee_id=invitee_id,
            created_at=now,
            updated_at=now,
        )
        await invite.insert()
        return invite

    async def get_invite_by_code(self, code: str) -> InviteLinkDocument | None:
        return await InviteLinkDocument.find_one({"code": code, "target_type": "space"})

    async def get_invite_by_id(self, *, invite_id: str) -> InviteLinkDocument | None:
        return await InviteLinkDocument.find_one(
            {"_id": parse_object_id(invite_id), "target_type": "space"}
        )

    async def list_invites_for_space(self, *, space_id: str) -> list[InviteLinkDocument]:
        return await InviteLinkDocument.find(
            {"target_type": "space", "target_id": str(space_id)}
        ).to_list()

    async def revoke_invite(self, *, invite_id: str) -> bool:
        now = datetime.now(UTC)
        result = await InviteLinkDocument.get_pymongo_collection().update_one(
            {"_id": parse_object_id(invite_id), "target_type": "space"},
            {"$set": {"revoked": True, "updated_at": now}},
        )
        return result.modified_count > 0

    async def consume_invite_use(self, *, code: str) -> InviteLinkDocument | None:
        now = datetime.now(UTC)
        raw = await InviteLinkDocument.get_pymongo_collection().find_one_and_update(
            {
                "code": code,
                "target_type": "space",
                "revoked": False,
                "$and": [
                    {"$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}]},
                    {
                        "$or": [
                            {"max_uses": None},
                            {"$expr": {"$lt": ["$use_count", "$max_uses"]}},
                        ]
                    },
                ],
            },
            {"$inc": {"use_count": 1}, "$set": {"updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return InviteLinkDocument.model_validate(raw) if raw is not None else None

    async def get_pending_join_request(
        self, *, space_id: str, user_id: str
    ) -> JoinRequestDocument | None:
        doc = await RelationshipDocument.find_one(
            {**_space_membership_filter(space_id, user_id), "status": "pending"}
        )
        return to_join_request(doc) if doc is not None else None

    async def create_join_request(
        self, *, space_id: str, user_id: str, invite_code: str | None = None
    ) -> JoinRequestDocument:
        """Open a pending space membership — the join request itself (§13-17)."""
        doc = await RelationshipService().request(
            kind="membership",
            user_id=user_id,
            target_type="space",
            target_id=space_id,
        )
        if invite_code is not None:
            updated = await RelationshipsRepository().find_one_and_update(
                {"_id": doc.id},
                {
                    "$set": {
                        "invite_code": invite_code,
                        "updated_at": datetime.now(UTC),
                    }
                },
            )
            doc = updated or doc
        return to_join_request(doc)

    async def get_join_request(self, *, request_id: str) -> JoinRequestDocument | None:
        doc = await RelationshipDocument.find_one(
            {
                "_id": parse_object_id(request_id),
                "kind": "membership",
                "target_type": "space",
            }
        )
        return to_join_request(doc) if doc is not None else None

    async def list_pending_join_requests(self, *, space_id: str) -> list[JoinRequestDocument]:
        docs = await RelationshipDocument.find(
            {
                "kind": "membership",
                "target_type": "space",
                "target_id": str(space_id),
                "status": "pending",
            }
        ).to_list()
        return [to_join_request(doc) for doc in docs]

    async def set_join_request_status(
        self, *, request_id: str, status: str
    ) -> JoinRequestDocument | None:
        """Resolve a pending membership; `approved`/`rejected` map to the
        relationship lifecycle's `active`/`declined`."""
        now = datetime.now(UTC)
        raw = await RelationshipDocument.get_pymongo_collection().find_one_and_update(
            {
                "_id": parse_object_id(request_id),
                "kind": "membership",
                "target_type": "space",
                "status": "pending",
            },
            {
                "$set": {
                    "status": _RELATIONSHIP_STATUS_BY_REQUEST_STATUS[status],
                    "activated_at": now if status == "approved" else None,
                    "ended_at": None if status == "approved" else now,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            return None
        return to_join_request(RelationshipDocument.model_validate(raw))

    async def update_space(
        self,
        *,
        space_id: str,
        name: str | None = None,
        visibility: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> SpaceDocument | None:
        now = datetime.now(UTC)
        updates: dict[str, Any] = {"updated_at": now}
        if name is not None:
            updates["name"] = name.strip()
        if visibility is not None:
            updates["visibility"] = visibility
        if settings is not None:
            updates["settings"] = settings

        raw = await SpaceDocument.get_pymongo_collection().find_one_and_update(
            {"_id": parse_object_id(space_id)},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        return SpaceDocument.model_validate(raw) if raw is not None else None
