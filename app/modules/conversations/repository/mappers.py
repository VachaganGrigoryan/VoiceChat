from __future__ import annotations

from app.db.models import (
    ConversationDocument,
    InviteLinkDocument,
    JoinRequestDocument,
    ParticipantDocument,
)
from app.modules.conversations.group_avatar import build_group_avatar_payload
from app.modules.conversations.schemas import (
    ConversationPreview,
    ConversationUserSummary,
    ConversationView,
    InviteLinkView,
    JoinRequestView,
    ParticipantView,
)


def to_conversation_view(
    conversation: ConversationDocument,
    *,
    unread_count: int = 0,
    peer_user: ConversationUserSummary | None = None,
    participant_users: list[ConversationUserSummary] | None = None,
    viewer_participant: ParticipantDocument | None = None,
) -> ConversationView:
    preview = conversation.last_message_preview
    preview_view = (
        ConversationPreview(
            message_id=preview.message_id,
            sender_id=str(preview.sender_id),
            type=preview.type,
            text=preview.text,
            created_at=preview.created_at,
        )
        if preview is not None
        else None
    )

    return ConversationView(
        id=conversation.str_id,
        type=conversation.type,
        encryption=conversation.encryption,
        participant_ids=[str(pid) for pid in conversation.participant_ids],
        created_by=str(conversation.created_by),
        owner_type=conversation.owner.type if conversation.owner else None,
        owner_id=str(conversation.owner.id) if conversation.owner else None,
        title=conversation.title,
        image=build_group_avatar_payload(conversation.image),
        visibility=conversation.visibility,
        posting_policy=conversation.posting_policy,
        read_policy=getattr(conversation, "read_policy", "members"),
        space_id=(
            str(conversation.space_id) if conversation.space_id is not None else None
        ),
        slug=conversation.slug,
        description=conversation.description,
        member_count=conversation.member_count,
        pinned_message_ids=conversation.pinned_message_ids,
        settings=conversation.settings,
        peer_user=peer_user,
        participant_users=participant_users or [],
        last_message_at=conversation.last_message_at,
        last_message_preview=preview_view,
        unread_count=unread_count,
        notification_level=(
            viewer_participant.notification_level
            if viewer_participant is not None
            else "all"
        ),
        muted_until=(
            viewer_participant.muted_until if viewer_participant is not None else None
        ),
        pinned=viewer_participant.pinned if viewer_participant is not None else False,
        archived=(
            viewer_participant.archived if viewer_participant is not None else False
        ),
        folder=viewer_participant.folder if viewer_participant is not None else None,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def to_invite_link_view(invite: InviteLinkDocument) -> InviteLinkView:
    return InviteLinkView(
        id=invite.str_id,
        conversation_id=str(invite.target_id),
        code=invite.code,
        created_by=str(invite.created_by),
        expires_at=invite.expires_at,
        max_uses=invite.max_uses,
        use_count=invite.use_count,
        requires_approval=invite.requires_approval,
        revoked=invite.revoked,
        created_at=invite.created_at,
        updated_at=invite.updated_at,
    )


def to_join_request_view(request: JoinRequestDocument) -> JoinRequestView:
    return JoinRequestView(
        id=request.str_id,
        conversation_id=str(request.target_id),
        user_id=str(request.user_id),
        status=request.status,
        invite_code=request.invite_code,
        responded_at=request.responded_at,
        created_at=request.created_at,
    )


def to_participant_view(participant: ParticipantDocument) -> ParticipantView:
    return ParticipantView(
        conversation_id=str(participant.conversation_id),
        user_id=str(participant.user_id),
        role=participant.role,
        permissions=participant.permissions,
        joined_at=participant.joined_at,
        last_read_at=participant.last_read_at,
        last_read_message_id=participant.last_read_message_id,
        notification_level=participant.notification_level,
        muted_until=participant.muted_until,
        archived=participant.archived,
        pinned=participant.pinned,
        folder=participant.folder,
        invited_by=(
            str(participant.invited_by) if participant.invited_by is not None else None
        ),
        draft_text=participant.draft_text,
        draft_updated_at=participant.draft_updated_at,
        muted=participant.muted,
        hidden=participant.hidden,
    )
