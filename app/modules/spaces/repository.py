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
)
from app.db.object_id import parse_object_id
from app.db.repository import BaseRepository

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
        try:
            raw = await SpaceMemberDocument.get_pymongo_collection().find_one_and_update(
                {"space_id": str(space_id), "user_id": str(user_id)},
                {
                    "$set": {
                        "role": role,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"joined_at": now, "created_at": now},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            raw = await SpaceMemberDocument.get_pymongo_collection().find_one(
                {"space_id": str(space_id), "user_id": str(user_id)}
            )
        return SpaceMemberDocument.model_validate(raw)

    async def get_membership(self, *, space_id: str, user_id: str) -> SpaceMemberDocument | None:
        return await SpaceMemberDocument.find_one(
            {"space_id": str(space_id), "user_id": str(user_id)}
        )

    async def list_memberships_for_user(self, *, user_id: str) -> list[SpaceMemberDocument]:
        return await SpaceMemberDocument.find({"user_id": str(user_id)}).to_list()

    async def list_members_for_space(self, *, space_id: str) -> list[SpaceMemberDocument]:
        return await SpaceMemberDocument.find({"space_id": str(space_id)}).to_list()

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
        return await JoinRequestDocument.find_one(
            {
                "target_type": "space",
                "target_id": str(space_id),
                "user_id": str(user_id),
                "status": "pending",
            }
        )

    async def create_join_request(
        self, *, space_id: str, user_id: str, invite_code: str | None = None
    ) -> JoinRequestDocument:
        now = datetime.now(UTC)
        request = JoinRequestDocument(
            target_type="space",
            target_id=str(space_id),
            user_id=str(user_id),
            invite_code=invite_code,
            created_at=now,
            updated_at=now,
        )
        await request.insert()
        return request

    async def get_join_request(self, *, request_id: str) -> JoinRequestDocument | None:
        return await JoinRequestDocument.find_one(
            {"_id": parse_object_id(request_id), "target_type": "space"}
        )

    async def list_pending_join_requests(self, *, space_id: str) -> list[JoinRequestDocument]:
        return await JoinRequestDocument.find(
            {
                "target_type": "space",
                "target_id": str(space_id),
                "status": "pending",
            }
        ).to_list()

    async def set_join_request_status(
        self, *, request_id: str, status: str
    ) -> JoinRequestDocument | None:
        now = datetime.now(UTC)
        raw = await JoinRequestDocument.get_pymongo_collection().find_one_and_update(
            {"_id": parse_object_id(request_id), "target_type": "space", "status": "pending"},
            {"$set": {"status": status, "responded_at": now, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return JoinRequestDocument.model_validate(raw) if raw is not None else None

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
