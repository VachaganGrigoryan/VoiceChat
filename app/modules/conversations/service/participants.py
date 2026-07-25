from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import AppError
from app.db.models import ConversationDocument, ParticipantDocument
from app.infra.storage import get_storage, storage_key_builder
from app.modules.conversations.group_avatar import read_group_image_upload
from app.modules.conversations.permissions import (
    CONVERSATION_RIGHTS,
    participant_can,
)
from app.modules.conversations.service.base import BaseConversationsService


class ParticipantsServiceMixin(BaseConversationsService):
    async def require_permission(
        self, *, user_id: str, conversation_id: str, right: str
    ) -> ParticipantDocument:
        """Shared granular-permission gate.

        Authorizes ``right`` for the caller by their base role refined by their
        optional per-participant ``permissions`` map. Raises 403 when denied.
        """
        participant = await self._get_participant_or_404(
            conversation_id=conversation_id, user_id=user_id
        )
        if not participant_can(
            role=participant.role, permissions=participant.permissions, right=right
        ):
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message=f"Not permitted: {right}",
                status_code=403,
            )
        return participant

    async def set_member_permissions(
        self,
        *,
        actor_user_id: str,
        conversation_id: str,
        target_user_id: str,
        permissions: dict | None,
    ) -> ParticipantDocument:
        """Set (or clear) a member's granular permissions map. Owner-only."""
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner"},
        )
        await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        if permissions is not None:
            unknown = set(permissions) - set(CONVERSATION_RIGHTS)
            if unknown:
                raise AppError(
                    code="INVALID_PERMISSIONS",
                    message=f"Unknown permissions: {', '.join(sorted(unknown))}",
                    status_code=400,
                )
        updated = await self.repo.set_participant_permissions(
            conversation_id=conversation.str_id,
            user_id=target_user_id,
            permissions=permissions,
        )
        assert updated is not None
        return updated
    async def _get_group_for_participant(
        self, *, conversation_id: str, user_id: str
    ) -> ConversationDocument:
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        if conversation.type != "group":
            raise AppError(
                code="CONVERSATION_NOT_GROUP",
                message="Conversation is not a group",
                status_code=400,
            )
        return conversation

    async def _get_participant_or_404(
        self, *, conversation_id: str, user_id: str
    ) -> ParticipantDocument:
        participant = await self.repo.get_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if participant is None:
            raise AppError(
                code="PARTICIPANT_NOT_FOUND",
                message="Participant not found",
                status_code=404,
            )
        return participant

    async def _require_actor_role(
        self,
        *,
        conversation_id: str,
        user_id: str,
        allowed_roles: set[str],
    ) -> ParticipantDocument:
        participant = await self._get_participant_or_404(
            conversation_id=conversation_id, user_id=user_id
        )
        if participant.role not in allowed_roles:
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Not allowed to manage this conversation",
                status_code=403,
            )
        return participant

    async def require_group_manager(
        self,
        *,
        user_id: str,
        conversation_id: str,
        allowed_roles: set[str],
    ) -> ConversationDocument:
        """Load a group and assert the caller holds one of ``allowed_roles``.

        Shared entry point for group-management actions that live outside this
        service (e.g. clearing history for everyone via the messages service).
        """
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=user_id,
            allowed_roles=allowed_roles,
        )
        return conversation

    async def set_allow_member_polls(
        self, *, actor_user_id: str, conversation_id: str, allow: bool
    ) -> ConversationDocument:
        """Toggle whether non-admin members may create polls in a group/channel.

        Owner/admin only. Unlike ``require_group_manager`` (group-only) this also
        covers channels, since both restrict poll creation to admins by default.
        """
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        if conversation.type not in {"group", "channel"}:
            raise AppError(
                code="CONVERSATION_SETTINGS_UNSUPPORTED",
                message="This setting only applies to groups and channels",
                status_code=400,
            )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner", "admin"},
        )
        return await self.repo.set_conversation_setting(
            conversation_id=conversation.str_id,
            key="allow_member_polls",
            value=allow,
        )

    async def rename_group(
        self, *, actor_user_id: str, conversation_id: str, title: str
    ) -> ConversationDocument:
        conversation = await self.require_group_manager(
            user_id=actor_user_id,
            conversation_id=conversation_id,
            allowed_roles={"owner", "admin"},
        )
        return await self.repo.update_group_title(
            conversation_id=conversation.str_id, title=title.strip()
        )

    async def set_group_avatar(
        self, *, actor_user_id: str, conversation_id: str, file: UploadFile
    ) -> ConversationDocument:
        conversation = await self.require_group_manager(
            user_id=actor_user_id,
            conversation_id=conversation_id,
            allowed_roles={"owner", "admin"},
        )

        content, content_type = await read_group_image_upload(file)
        storage = get_storage()
        key_builder = storage_key_builder("avatar")
        stored = await storage.save(
            filename=file.filename,
            content=content,
            mime=content_type,
            key=key_builder(conversation.str_id, file.filename),
        )
        image = {
            "storage": stored.storage,
            "key": stored.key,
            "url": stored.url,
            "mime": stored.mime,
            "size_bytes": stored.size_bytes,
        }

        previous = conversation.image
        updated = await self.repo.update_group_image(
            conversation_id=conversation.str_id, image=image
        )

        if previous and isinstance(previous, dict):
            prev_key = previous.get("key")
            if prev_key and prev_key != image["key"]:
                try:
                    await storage.delete(prev_key)
                except Exception:  # noqa: BLE001 - best-effort cleanup, never fatal
                    pass

        return updated

    async def remove_group_avatar(
        self, *, actor_user_id: str, conversation_id: str
    ) -> ConversationDocument:
        conversation = await self.require_group_manager(
            user_id=actor_user_id,
            conversation_id=conversation_id,
            allowed_roles={"owner", "admin"},
        )

        previous = conversation.image
        updated = await self.repo.update_group_image(
            conversation_id=conversation.str_id, image=None
        )

        if previous and isinstance(previous, dict):
            prev_key = previous.get("key")
            if prev_key:
                try:
                    await get_storage(previous.get("storage")).delete(prev_key)
                except Exception:  # noqa: BLE001 - best-effort cleanup, never fatal
                    pass

        return updated

    async def mark_conversation_read(
        self,
        *,
        user_id: str,
        conversation_id: str,
        last_read_message_id: str | None = None,
    ) -> None:
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        await self.repo.mark_read(
            conversation_id=conversation.str_id,
            user_id=user_id,
            last_read_message_id=last_read_message_id,
        )

    async def set_inbox_state(
        self, *, user_id: str, conversation_id: str, updates: dict
    ) -> ParticipantDocument:
        """Update the caller's per-participant inbox flags (pin/archive/folder)."""
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        updated = await self.repo.update_participant_inbox_state(
            conversation_id=conversation.str_id, user_id=user_id, updates=updates
        )
        if updated is None:
            raise AppError(
                code="PARTICIPANT_NOT_FOUND",
                message="Participant not found",
                status_code=404,
            )
        return updated

    async def set_inbox_state_bulk(
        self, *, user_id: str, conversation_ids: list[str], updates: dict
    ) -> int:
        """Apply the caller's inbox flags to several conversations at once.

        Membership is enforced implicitly: only the caller's own participant rows
        are matched, so ids the caller isn't part of are silently skipped.
        Returns the number of conversations updated.
        """
        return await self.repo.update_many_inbox_state(
            conversation_ids=conversation_ids, user_id=user_id, updates=updates
        )

    async def resurface_on_send(
        self, *, user_id: str, conversation_id: str
    ) -> None:
        """Auto-unarchive the sender's view when they reply to an archived chat."""
        await self.repo.clear_archived_if_set(
            conversation_id=conversation_id, user_id=user_id
        )

    async def list_folders(self, *, user_id: str) -> list[dict]:
        """List the caller's folders with total and archived conversation counts."""
        return await self.repo.aggregate_folders(user_id=user_id)

    async def rename_folder(
        self, *, user_id: str, old_name: str, new_name: str
    ) -> int:
        """Rename a folder across all the caller's conversations."""
        return await self.repo.rename_folder(
            user_id=user_id, old_name=old_name, new_name=new_name
        )

    async def delete_folder(self, *, user_id: str, name: str) -> int:
        """Clear a folder label from all the caller's conversations."""
        return await self.repo.clear_folder(user_id=user_id, name=name)

    async def list_group_participants(
        self, *, user_id: str, conversation_id: str
    ) -> list[ParticipantDocument]:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        return await self.repo.list_participants(conversation_id=conversation.str_id)

    async def add_group_members(
        self, *, actor_user_id: str, conversation_id: str, participant_ids: list[str]
    ) -> list[ParticipantDocument]:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner", "admin"},
        )

        added: list[ParticipantDocument] = []
        for participant_id in sorted(
            {str(pid) for pid in participant_ids if str(pid) != actor_user_id}
        ):
            space_id = conversation.space_id
            skip_ping_check = False
            if space_id is not None:
                from app.db.models.space_member import SpaceMemberDocument
                p_member = await SpaceMemberDocument.find_one({
                    "space_id": str(space_id),
                    "user_id": str(participant_id)
                })
                if p_member is not None:
                    skip_ping_check = True

            if not skip_ping_check:
                await self._ensure_can_message(
                    sender_id=actor_user_id, receiver_id=participant_id
                )
            participant = await self.repo.ensure_participant(
                conversation_id=conversation.str_id,
                user_id=participant_id,
                role="member",
            )
            await self.repo.add_participant_id(
                conversation_id=conversation.str_id,
                user_id=participant_id,
            )
            added.append(participant)
        
        if added:
            from app.db.models import AuditLogDocument
            log = AuditLogDocument(
                actor_id=actor_user_id,
                action="add_members",
                target_type="conversation",
                target_id=conversation.str_id,
                space_id=getattr(conversation, "space_id", None),
                data={"added_user_ids": [p.str_id for p in added]},
            )
            await log.insert()

        return added

    async def remove_group_member(
        self, *, actor_user_id: str, conversation_id: str, target_user_id: str
    ) -> None:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        actor = await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner", "admin"},
        )
        target = await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )

        if target.role == "owner" or (actor.role == "admin" and target.role != "member"):
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Not allowed to remove this participant",
                status_code=403,
            )

        await self.repo.delete_participant(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        await self.repo.remove_participant_id(
            conversation_id=conversation.str_id, user_id=target_user_id
        )

        from app.db.models import AuditLogDocument
        log = AuditLogDocument(
            actor_id=actor_user_id,
            action="remove_member",
            target_type="conversation",
            target_id=conversation.str_id,
            space_id=getattr(conversation, "space_id", None),
            data={"removed_user_id": target_user_id},
        )
        await log.insert()

    async def update_group_member_role(
        self,
        *,
        actor_user_id: str,
        conversation_id: str,
        target_user_id: str,
        role: str,
    ) -> ParticipantDocument:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner"},
        )
        target = await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        if target.role == "owner":
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Owner role can only change through ownership transfer",
                status_code=403,
            )
        updated = await self.repo.set_participant_role(
            conversation_id=conversation.str_id,
            user_id=target_user_id,
            role=role,
        )
        assert updated is not None

        from app.db.models import AuditLogDocument
        log = AuditLogDocument(
            actor_id=actor_user_id,
            action="update_member_role",
            target_type="conversation",
            target_id=conversation.str_id,
            space_id=getattr(conversation, "space_id", None),
            data={"target_user_id": target_user_id, "role": role},
        )
        await log.insert()

        return updated

    async def transfer_group_ownership(
        self, *, actor_user_id: str, conversation_id: str, target_user_id: str
    ) -> list[ParticipantDocument]:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner"},
        )
        await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        if actor_user_id == target_user_id:
            raise AppError(
                code="INVALID_CONVERSATION",
                message="Cannot transfer ownership to yourself",
                status_code=400,
            )

        new_owner = await self.repo.set_participant_role(
            conversation_id=conversation.str_id,
            user_id=target_user_id,
            role="owner",
        )
        previous_owner = await self.repo.set_participant_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            role="admin",
        )
        assert new_owner is not None and previous_owner is not None

        from app.db.models import AuditLogDocument
        log = AuditLogDocument(
            actor_id=actor_user_id,
            action="transfer_ownership",
            target_type="conversation",
            target_id=conversation.str_id,
            space_id=getattr(conversation, "space_id", None),
            data={"new_owner_id": target_user_id, "previous_owner_id": actor_user_id},
        )
        await log.insert()

        return [new_owner, previous_owner]

    async def leave_group(self, *, user_id: str, conversation_id: str) -> None:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        participant = await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=user_id
        )
        if participant.role == "owner":
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Transfer ownership before leaving the group",
                status_code=403,
            )
        await self.repo.delete_participant(
            conversation_id=conversation.str_id, user_id=user_id
        )
        await self.repo.remove_participant_id(
            conversation_id=conversation.str_id, user_id=user_id
        )

        from app.db.models import AuditLogDocument
        log = AuditLogDocument(
            actor_id=user_id,
            action="leave_group",
            target_type="conversation",
            target_id=conversation.str_id,
            space_id=getattr(conversation, "space_id", None),
            data={},
        )
        await log.insert()

    async def delete_group(self, *, user_id: str, conversation_id: str) -> None:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=user_id,
            allowed_roles={"owner"},
        )
        participants = await self.repo.list_participants(
            conversation_id=conversation.str_id
        )
        for participant in participants:
            await self.repo.delete_participant(
                conversation_id=conversation.str_id,
                user_id=str(participant.user_id),
            )
        await self.repo.delete_conversation(conversation_id=conversation.str_id)

        from app.db.models import AuditLogDocument
        log = AuditLogDocument(
            actor_id=user_id,
            action="delete_group",
            target_type="conversation",
            target_id=conversation.str_id,
            space_id=getattr(conversation, "space_id", None),
            data={},
        )
        await log.insert()
