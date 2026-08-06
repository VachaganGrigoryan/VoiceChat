from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument

from app.db.models import MessageDocument, ParticipantDocument, RelationshipDocument
from app.modules.relationships.compat import (
    DEFAULT_ROLE as _DEFAULT_ROLE,
    membership_filter,
    resolve_role_names,
    to_participant,
)


async def _role_id_for(conversation_id: str, role_name: str) -> str:
    """The conversation-scoped role id for a role name, seeding if needed."""
    from app.modules.authorization.roles import RoleService

    return await RoleService().resolve_role_id(
        scope_type="conversation", scope_id=conversation_id, name=role_name
    )


def _membership_filter(conversation_id: str, user_id: str) -> dict:
    return membership_filter(
        target_type="conversation", target_id=conversation_id, user_id=user_id
    )


async def _project(relationship: RelationshipDocument) -> ParticipantDocument:
    """Project one membership, resolving its role ids to a role name."""
    return to_participant(
        relationship, role_names=await resolve_role_names([relationship])
    )


async def _to_participant_or_none(raw: dict | None) -> ParticipantDocument | None:
    if raw is None:
        return None
    return await _project(RelationshipDocument.model_validate(raw))


def _state_updates(updates: dict, allowed: tuple[str, ...]) -> dict:
    """Map legacy flat participant keys onto dotted ``state.*`` paths."""
    return {
        f"state.{key}": updates[key] for key in allowed if key in updates
    }


class ParticipantsRepositoryMixin:
    """Conversation participation backed by `Relationship(kind=membership)`.

    Only `status = active` memberships count as participation (§58); the
    per-user inbox/read fields live on the membership `state` bag (§65).
    """

    @property
    def _relationships(self):
        return RelationshipDocument.get_pymongo_collection()

    async def ensure_participant(
        self, *, conversation_id: str, user_id: str, role: str | None = _DEFAULT_ROLE
    ) -> ParticipantDocument:
        """Make ``user_id`` an active participant holding the named role.

        ``role=None`` assigns no role — what DMs use, since both sides are
        peers and their access comes from membership and the conversation's
        posting policy rather than from RBAC.
        """
        now = datetime.now(UTC)
        role_id = await _role_id_for(conversation_id, role) if role else None
        raw = await self._relationships.find_one_and_update(
            _membership_filter(conversation_id, user_id),
            {
                "$set": {"status": "active", "activated_at": now, "updated_at": now},
                "$setOnInsert": {
                    "kind": "membership",
                    "target_type": "conversation",
                    "target_id": str(conversation_id),
                    "user_id": str(user_id),
                    "role_ids": [role_id] if role_id else [],
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
        return await _project(RelationshipDocument.model_validate(raw))

    async def get_participant(
        self, *, conversation_id: str, user_id: str
    ) -> ParticipantDocument | None:
        raw = await self._relationships.find_one(
            {**_membership_filter(conversation_id, user_id), "status": "active"}
        )
        return await _to_participant_or_none(raw)

    async def list_participants(
        self, *, conversation_id: str
    ) -> list[ParticipantDocument]:
        docs = await RelationshipDocument.find(
            {
                "kind": "membership",
                "target_type": "conversation",
                "target_id": str(conversation_id),
                "status": "active",
            }
        ).to_list()
        role_names = await resolve_role_names(docs)
        return [to_participant(doc, role_names=role_names) for doc in docs]

    async def set_participant_role(
        self, *, conversation_id: str, user_id: str, role: str
    ) -> ParticipantDocument | None:
        """Replace a member's roles with the single role named ``role``."""
        role_id = await _role_id_for(conversation_id, role)
        raw = await self._relationships.find_one_and_update(
            _membership_filter(conversation_id, user_id),
            {"$set": {"role_ids": [role_id], "updated_at": datetime.now(UTC)}},
            return_document=ReturnDocument.AFTER,
        )
        return await _to_participant_or_none(raw)

    async def set_participant_permissions(
        self, *, conversation_id: str, user_id: str, permissions: dict | None
    ) -> ParticipantDocument | None:
        overrides = (
            None
            if permissions is None
            else {
                "allow": [name for name, granted in permissions.items() if granted],
                "deny": [name for name, granted in permissions.items() if not granted],
            }
        )
        raw = await self._relationships.find_one_and_update(
            _membership_filter(conversation_id, user_id),
            {
                "$set": {
                    "permission_overrides": overrides,
                    "updated_at": datetime.now(UTC),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return await _to_participant_or_none(raw)

    async def delete_participant(self, *, conversation_id: str, user_id: str) -> bool:
        result = await self._relationships.delete_one(
            _membership_filter(conversation_id, user_id)
        )
        return result.deleted_count > 0

    async def update_participant_inbox_state(
        self, *, conversation_id: str, user_id: str, updates: dict
    ) -> ParticipantDocument | None:
        """Set the caller's ``pinned``/``archived``/``folder`` inbox flags."""
        set_fields = _state_updates(updates, ("pinned", "archived", "folder"))
        set_fields["updated_at"] = datetime.now(UTC)
        raw = await self._relationships.find_one_and_update(
            _membership_filter(conversation_id, user_id),
            {"$set": set_fields},
            return_document=ReturnDocument.AFTER,
        )
        return await _to_participant_or_none(raw)

    async def update_many_inbox_state(
        self, *, conversation_ids: list[str], user_id: str, updates: dict
    ) -> int:
        """Apply whitelisted inbox flags to several of the caller's rows at once.

        Returns the number of membership rows modified.
        """
        set_fields = _state_updates(updates, ("pinned", "archived", "folder"))
        if not set_fields:
            return 0
        set_fields["updated_at"] = datetime.now(UTC)
        result = await self._relationships.update_many(
            {
                "kind": "membership",
                "target_type": "conversation",
                "target_id": {"$in": [str(cid) for cid in conversation_ids]},
                "user_id": str(user_id),
            },
            {"$set": set_fields},
        )
        return result.modified_count

    async def clear_archived_if_set(
        self, *, conversation_id: str, user_id: str
    ) -> ParticipantDocument | None:
        """Unarchive the caller's row only if it is currently archived.

        Conditional so ordinary sends to non-archived conversations perform no
        write and don't churn ``updated_at``. Returns the updated row, or ``None``
        when nothing was archived.
        """
        raw = await self._relationships.find_one_and_update(
            {**_membership_filter(conversation_id, user_id), "state.archived": True},
            {
                "$set": {
                    "state.archived": False,
                    "updated_at": datetime.now(UTC),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return await _to_participant_or_none(raw)

    async def aggregate_folders(self, *, user_id: str) -> list[dict]:
        """Return the caller's folders with total and archived conversation counts.

        Discovered from the per-membership ``state.folder`` label, so folders
        survive even when all their conversations are archived or past a page.
        """
        cursor = await self._relationships.aggregate(
            [
                {
                    "$match": {
                        "kind": "membership",
                        "target_type": "conversation",
                        "user_id": str(user_id),
                        "state.folder": {"$ne": None},
                    }
                },
                {
                    "$group": {
                        "_id": "$state.folder",
                        "count": {"$sum": 1},
                        "archived_count": {
                            "$sum": {"$cond": ["$state.archived", 1, 0]},
                        },
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
        return [
            {
                "name": row["_id"],
                "count": row["count"],
                "archived_count": row["archived_count"],
            }
            async for row in cursor
        ]

    async def rename_folder(self, *, user_id: str, old_name: str, new_name: str) -> int:
        """Rename a folder across all the caller's conversations."""
        result = await self._relationships.update_many(
            {
                "kind": "membership",
                "target_type": "conversation",
                "user_id": str(user_id),
                "state.folder": old_name,
            },
            {"$set": {"state.folder": new_name, "updated_at": datetime.now(UTC)}},
        )
        return result.modified_count

    async def clear_folder(self, *, user_id: str, name: str) -> int:
        """Remove a folder label from all the caller's conversations."""
        result = await self._relationships.update_many(
            {
                "kind": "membership",
                "target_type": "conversation",
                "user_id": str(user_id),
                "state.folder": name,
            },
            {"$set": {"state.folder": None, "updated_at": datetime.now(UTC)}},
        )
        return result.modified_count

    async def set_participant_draft(
        self,
        *,
        conversation_id: str,
        user_id: str,
        draft_text: str | None,
        draft_updated_at: datetime | None,
    ) -> ParticipantDocument | None:
        """Set or clear the caller's per-conversation draft."""
        raw = await self._relationships.find_one_and_update(
            _membership_filter(conversation_id, user_id),
            {
                "$set": {
                    "state.draft_text": draft_text,
                    "state.draft_updated_at": draft_updated_at,
                    "updated_at": datetime.now(UTC),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return await _to_participant_or_none(raw)

    async def mark_read(
        self,
        *,
        conversation_id: str,
        user_id: str,
        last_read_message_id: str | None,
        last_read_at: datetime | None = None,
    ) -> None:
        now = datetime.now(UTC)
        await self._relationships.update_one(
            _membership_filter(conversation_id, user_id),
            {
                "$set": {
                    "state.last_read_at": last_read_at or now,
                    "state.last_read_message_id": last_read_message_id,
                    "updated_at": now,
                }
            },
        )

    async def unread_count(
        self,
        *,
        message_conversation_id: str,
        user_id: str,
        last_read_at: datetime | None,
    ) -> int:
        """Count messages from other senders newer than the user's read cursor."""
        query: dict = {
            "container_type": "conversation",
            "container_id": str(message_conversation_id),
            "sender_id": {"$ne": str(user_id)},
            "thread_root_id": None,
            "state": {"$ne": "scheduled"},
        }
        if last_read_at is not None:
            query["created_at"] = {"$gt": last_read_at}
        return await MessageDocument.find(query).count()


__all__ = ["ParticipantsRepositoryMixin", "to_participant"]
