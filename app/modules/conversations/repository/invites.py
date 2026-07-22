from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument

from app.db.models import InviteLinkDocument, JoinRequestDocument
from app.db.object_id import parse_object_id


class InvitesRepositoryMixin:
    """Invite-link and join-request persistence for conversations.

    Operates on the ``invite_links`` / ``join_requests`` collections directly
    (independent of the conversations ``model``), mirroring how the participants
    mixin reaches into its own collections.
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
        return await JoinRequestDocument.find_one(
            {
                "target_type": "conversation",
                "target_id": str(conversation_id),
                "user_id": str(user_id),
                "status": "pending",
            }
        )

    async def create_join_request(
        self, *, conversation_id: str, user_id: str, invite_code: str | None
    ) -> JoinRequestDocument:
        now = datetime.now(UTC)
        request = JoinRequestDocument(
            target_type="conversation",
            target_id=str(conversation_id),
            user_id=str(user_id),
            invite_code=invite_code,
            created_at=now,
            updated_at=now,
        )
        await request.insert()
        return request

    async def get_join_request(self, *, request_id: str) -> JoinRequestDocument | None:
        return await JoinRequestDocument.find_one(
            {"_id": parse_object_id(request_id)}
        )

    async def list_pending_join_requests(
        self, *, conversation_id: str
    ) -> list[JoinRequestDocument]:
        return await JoinRequestDocument.find(
            {
                "target_type": "conversation",
                "target_id": str(conversation_id),
                "status": "pending",
            }
        ).to_list()

    async def set_join_request_status(
        self, *, request_id: str, status: str
    ) -> JoinRequestDocument | None:
        now = datetime.now(UTC)
        raw = await JoinRequestDocument.get_pymongo_collection().find_one_and_update(
            {"_id": parse_object_id(request_id), "status": "pending"},
            {"$set": {"status": status, "responded_at": now, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return JoinRequestDocument.model_validate(raw) if raw is not None else None
