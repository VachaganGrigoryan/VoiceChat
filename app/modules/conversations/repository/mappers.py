from __future__ import annotations

from app.db.models import ConversationDocument, ParticipantDocument
from app.modules.conversations.group_avatar import build_group_avatar_payload
from app.modules.conversations.schemas import (
    ConversationPreview,
    ConversationUserSummary,
    ConversationView,
    ParticipantView,
)


def to_conversation_view(
    conversation: ConversationDocument,
    *,
    unread_count: int = 0,
    peer_user: ConversationUserSummary | None = None,
    participant_users: list[ConversationUserSummary] | None = None,
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
        title=conversation.title,
        image=build_group_avatar_payload(conversation.image),
        peer_user=peer_user,
        participant_users=participant_users or [],
        last_message_at=conversation.last_message_at,
        last_message_preview=preview_view,
        unread_count=unread_count,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def to_participant_view(participant: ParticipantDocument) -> ParticipantView:
    return ParticipantView(
        conversation_id=str(participant.conversation_id),
        user_id=str(participant.user_id),
        role=participant.role,
        joined_at=participant.joined_at,
        last_read_at=participant.last_read_at,
        last_read_message_id=participant.last_read_message_id,
        muted=participant.muted,
        hidden=participant.hidden,
    )
