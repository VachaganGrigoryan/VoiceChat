from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument

from app.db.models import (
    InviteLinkDocument,
    JoinRequestDocument,
    RelationshipDocument,
)
from app.db.object_id import parse_object_id
from app.modules.relationships.compat import (
    RELATIONSHIP_STATUS_BY_REQUEST_STATUS as _RELATIONSHIP_STATUS_BY_REQUEST_STATUS,
    membership_filter,
    to_join_request,
)
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.service import RelationshipService


def _conversation_membership_filter(conversation_id: str, user_id: str) -> dict:
    return membership_filter(
        target_type="conversation", target_id=conversation_id, user_id=user_id
    )


class InvitesRepositoryMixin:
    """Invite-link and join-request persistence for conversations.

    Invite links live in ``invite_links``; a join request is a `pending`
    conversation membership in ``relationships`` (design §5), projected back
    into the legacy join-request shape for callers.
    """

    async def create_invite_link(
        self,
        *,
        target_id: str,
        created_by: str,
        code: str,
        expires_at: datetime | None,
        max_uses: int | None,
        requires_approval: bool,
    ) -> InviteLinkDocument:
        now = datetime.now(UTC)
        invite = InviteLinkDocument(
            target_type="conversation",
            target_id=str(target_id),
            code=code,
            created_by=str(created_by),
            expires_at=expires_at,
            max_uses=max_uses,
            requires_approval=requires_approval,
            created_at=now,
            updated_at=now,
        )
        await invite.insert()
        return invite

    async def get_invite_by_code(self, code: str) -> InviteLinkDocument | None:
        return await InviteLinkDocument.find_one({"code": code})

    async def get_invite_by_id(self, *, invite_id: str) -> InviteLinkDocument | None:
        return await InviteLinkDocument.find_one({"_id": parse_object_id(invite_id)})

    async def list_invites_for_conversation(
        self, *, conversation_id: str
    ) -> list[InviteLinkDocument]:
        return await InviteLinkDocument.find(
            {"target_type": "conversation", "target_id": str(conversation_id)}
        ).to_list()

    async def revoke_invite(self, *, invite_id: str) -> bool:
        now = datetime.now(UTC)
        result = await InviteLinkDocument.get_pymongo_collection().update_one(
            {"_id": parse_object_id(invite_id)},
            {"$set": {"revoked": True, "updated_at": now}},
        )
        return result.modified_count > 0

    async def consume_invite_use(self, *, code: str) -> InviteLinkDocument | None:
        """Atomically increment ``use_count`` if the link is still redeemable.

        Returns the updated link, or ``None`` when it is revoked, expired, or has
        reached ``max_uses`` (race-safe via a single conditional find-and-modify).
        """
        now = datetime.now(UTC)
        raw = await InviteLinkDocument.get_pymongo_collection().find_one_and_update(
            {
                "code": code,
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
        self, *, conversation_id: str, user_id: str
    ) -> JoinRequestDocument | None:
        doc = await RelationshipDocument.find_one(
            {
                **_conversation_membership_filter(conversation_id, user_id),
                "status": "pending",
            }
        )
        return to_join_request(doc) if doc is not None else None

    async def create_join_request(
        self, *, conversation_id: str, user_id: str, invite_code: str | None
    ) -> JoinRequestDocument:
        """Open a pending conversation membership — the join request itself."""
        doc = await RelationshipService().request(
            kind="membership",
            user_id=user_id,
            target_type="conversation",
            target_id=conversation_id,
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
                "target_type": "conversation",
            }
        )
        return to_join_request(doc) if doc is not None else None

    async def list_pending_join_requests(
        self, *, conversation_id: str
    ) -> list[JoinRequestDocument]:
        docs = await RelationshipDocument.find(
            {
                "kind": "membership",
                "target_type": "conversation",
                "target_id": str(conversation_id),
                "status": "pending",
            }
        ).to_list()
        return [to_join_request(doc) for doc in docs]

    async def set_join_request_status(
        self, *, request_id: str, status: str
    ) -> JoinRequestDocument | None:
        now = datetime.now(UTC)
        raw = await RelationshipDocument.get_pymongo_collection().find_one_and_update(
            {
                "_id": parse_object_id(request_id),
                "kind": "membership",
                "target_type": "conversation",
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
