from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import AppError
from app.db.models import ConversationDocument, ParticipantDocument
from app.infra.storage import get_storage, storage_key_builder
from app.modules.conversations.group_avatar import read_group_image_upload
from app.modules.authorization.permissions import (
    MEMBER_INVITE,
    MEMBER_MANAGE,
    MEMBER_REMOVE,
    PERMISSIONS,
    RESOURCE_DELETE,
    RESOURCE_MANAGE,
    ROLE_MANAGE,
)
from app.modules.authorization.roles import ROLE_ADMIN, ROLE_MEMBER
from app.modules.conversations.service.base import BaseConversationsService


class ParticipantsServiceMixin(BaseConversationsService):
    async def require_permission(
        self, *, user_id: str, conversation_id: str, permission: str
    ) -> None:
        """Authorize ``permission`` on a conversation, or raise 403.

        Thin wrapper over `AuthorizationService.can` so call sites read in the
        permission vocabulary rather than in role names (§56).
        """
        await self.authorization.require(
            user_id,
            permission,
            "conversation",
            conversation_id,
            message=f"Not permitted: {permission}",
        )

    async def _require_owner(self, *, user_id: str, conversation_id: str) -> None:
        """Assert effective ownership — for actions only an owner may take (§51).

        Ownership transfer and the like are ownership questions, not RBAC ones,
        so they consult the resolver directly rather than a permission.
        """
        is_owner = await self.authorization.ownership.is_effective_owner(
            user_id=user_id, resource_type="conversation", resource_id=conversation_id
        )
        if not is_owner:
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Only the owner can perform this action",
                status_code=403,
            )

    async def _require_removable(
        self, *, conversation_id: str, actor_user_id: str, target_user_id: str
    ) -> None:
        """Guard who a manager may remove.

        The owner is never removable, and a manager may not remove a peer who
        can also manage the conversation — only the owner can.
        """
        ownership = self.authorization.ownership
        if await ownership.is_effective_owner(
            user_id=target_user_id,
            resource_type="conversation",
            resource_id=conversation_id,
        ):
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="The owner cannot be removed",
                status_code=403,
            )
        target_manages = await self.authorization.can(
            target_user_id, RESOURCE_MANAGE, "conversation", conversation_id
        )
        if not target_manages:
            return
        if not await ownership.is_effective_owner(
            user_id=actor_user_id,
            resource_type="conversation",
            resource_id=conversation_id,
        ):
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Not allowed to remove this participant",
                status_code=403,
            )

    async def set_member_permissions(
        self,
        *,
        actor_user_id: str,
        conversation_id: str,
        target_user_id: str,
        permissions: dict | None,
    ) -> ParticipantDocument:
        """Set (or clear) a member's permission overrides.

        The map is `{permission: granted}` in the permission vocabulary; it
        becomes the membership's `permission_overrides.allow`/`.deny`, which
        outrank roles in the resolution order (§55).
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
        await self.require_permission(
            user_id=actor_user_id,
            conversation_id=conversation.str_id,
            permission=MEMBER_MANAGE,
        )
        await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        if permissions is not None:
            unknown = set(permissions) - PERMISSIONS
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

    async def require_group_manager(
        self,
        *,
        user_id: str,
        conversation_id: str,
        permission: str = RESOURCE_MANAGE,
    ) -> ConversationDocument:
        """Load a group and authorize ``permission`` on it.

        Shared entry point for group-management actions that live outside this
        service (e.g. clearing history for everyone via the messages service).
        """
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        await self.require_permission(
            user_id=user_id,
            conversation_id=conversation.str_id,
            permission=permission,
        )
        return conversation

    async def set_allow_member_polls(
        self, *, actor_user_id: str, conversation_id: str, allow: bool
    ) -> ConversationDocument:
        """Toggle whether non-admin members may create polls in a group/channel.

        Requires `resource.manage`. Unlike ``require_group_manager``
        (group-only) this also covers channels.
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
        await self.require_permission(
            user_id=actor_user_id,
            conversation_id=conversation.str_id,
            permission=RESOURCE_MANAGE,
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
            user_id=actor_user_id, conversation_id=conversation_id
        )
        return await self.repo.update_group_title(
            conversation_id=conversation.str_id, title=title.strip()
        )

    async def set_group_avatar(
        self, *, actor_user_id: str, conversation_id: str, file: UploadFile
    ) -> ConversationDocument:
        conversation = await self.require_group_manager(
            user_id=actor_user_id, conversation_id=conversation_id
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
            user_id=actor_user_id, conversation_id=conversation_id
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
        await self.require_permission(
            user_id=actor_user_id,
            conversation_id=conversation.str_id,
            permission=MEMBER_INVITE,
        )

        added: list[ParticipantDocument] = []
        for participant_id in sorted(
            {str(pid) for pid in participant_ids if str(pid) != actor_user_id}
        ):
            space_id = conversation.space_id
            skip_ping_check = False
            if space_id is not None:
                from app.modules.spaces.repository import find_active_space_membership
                p_member = await find_active_space_membership(
                    space_id=str(space_id), user_id=str(participant_id)
                )
                if p_member is not None:
                    skip_ping_check = True

            if not skip_ping_check:
                await self._ensure_can_message(
                    sender_id=actor_user_id, receiver_id=participant_id
                )
            participant = await self.repo.ensure_participant(
                conversation_id=conversation.str_id,
                user_id=participant_id,
                role=ROLE_MEMBER,
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
        await self.require_permission(
            user_id=actor_user_id,
            conversation_id=conversation.str_id,
            permission=MEMBER_REMOVE,
        )
        await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )

        # The owner is removable by nobody, and only the owner may remove
        # someone who can manage the conversation.
        await self._require_removable(
            conversation_id=conversation.str_id,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
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
        await self.require_permission(
            user_id=actor_user_id,
            conversation_id=conversation.str_id,
            permission=ROLE_MANAGE,
        )
        await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        if await self.authorization.ownership.is_effective_owner(
            user_id=target_user_id,
            resource_type="conversation",
            resource_id=conversation.str_id,
        ):
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="The owner's standing changes only through ownership transfer",
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
        await self._require_owner(
            user_id=actor_user_id, conversation_id=conversation.str_id
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

        # Ownership moves on the resource; both parties keep the Admin role so
        # the outgoing owner retains management standing (§51).
        await self.repo.set_owner_user(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        new_owner = await self.repo.set_participant_role(
            conversation_id=conversation.str_id,
            user_id=target_user_id,
            role=ROLE_ADMIN,
        )
        previous_owner = await self.repo.set_participant_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            role=ROLE_ADMIN,
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
        await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=user_id
        )
        if await self.authorization.ownership.is_effective_owner(
            user_id=user_id,
            resource_type="conversation",
            resource_id=conversation.str_id,
        ):
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
        await self.require_permission(
            user_id=user_id,
            conversation_id=conversation.str_id,
            permission=RESOURCE_DELETE,
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
