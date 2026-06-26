from __future__ import annotations

from datetime import UTC, datetime

from bson import ObjectId

from app.db.models import (
    CallMessageDocument,
    MediaDocument,
    MessageDocument,
    ReplyPreviewDocument,
)
from app.modules.messages.repository import MessagesRepository, conversation_id_for
from app.modules.messages.repository.mappers import (
    normalize_message_record,
    to_message_doc,
)

FIXED_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)


def _message_document(**overrides: object) -> MessageDocument:
    data = {
        "_id": ObjectId(),
        "conversation_id": "c1",
        "sender_id": "u1",
        "receiver_id": "u2",
        "type": "text",
        "text": None,
        "media": None,
        "call": None,
        "status": "sent",
        "hidden_for_user_ids": [],
        "created_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }
    data.update(overrides)
    return MessageDocument.model_validate(data)


def test_repository_package_exports_public_surface() -> None:
    repo = MessagesRepository()

    assert isinstance(repo, MessagesRepository)
    assert conversation_id_for("u2", "u1") == "u1_u2"


def test_normalize_message_record_keeps_canonical_type_and_media_kind() -> None:
    message = _message_document(
        type="media",
        media=MediaDocument(
            kind="voice",
            storage="local",
            key="voice/test.mp3",
            mime="audio/mpeg",
            size_bytes=123,
            duration_ms=1000,
        ),
    )

    normalized_type, normalized_media = normalize_message_record(message)

    assert normalized_type == "media"
    assert normalized_media is not None
    assert normalized_media.kind == "voice"
    assert normalized_media.url == "/media/voice/test.mp3"


def test_normalize_message_record_keeps_file_kind_for_file_messages() -> None:
    message = _message_document(
        type="file",
        media=MediaDocument(
            kind="file",
            storage="local",
            key="files/archive.zip",
            mime="application/zip",
            size_bytes=999,
            duration_ms=None,
        ),
    )

    normalized_type, normalized_media = normalize_message_record(message)

    assert normalized_type == "file"
    assert normalized_media is not None
    assert normalized_media.kind == "file"
    assert normalized_media.url == "/media/files/archive.zip"


def test_to_message_doc_uses_canonical_reply_preview_shape() -> None:
    message = _message_document(
        type="media",
        text="listen",
        media=MediaDocument(
            kind="audio",
            storage="local",
            key="audio/track.mp3",
            mime="audio/mpeg",
            size_bytes=321,
            duration_ms=5000,
        ),
        reply_mode="quote",
        reply_to_message_id="root1",
        reply_preview=ReplyPreviewDocument(
            message_id="root1",
            sender_id="u2",
            type="file",
            media_kind="file",
            text="archive",
            is_deleted=False,
        ),
        is_thread_root=False,
        thread_reply_count=0,
        last_thread_reply_at=None,
        reactions=[],
    )

    normalized_message = to_message_doc(message)

    assert normalized_message.type == "media"
    assert normalized_message.media is not None
    assert normalized_message.media.kind == "audio"
    assert normalized_message.reply_preview is not None
    assert normalized_message.reply_preview.type == "file"
    assert normalized_message.reply_preview.media_kind == "file"


def test_to_message_doc_normalizes_call_payload() -> None:
    message = _message_document(
        type="call",
        call=CallMessageDocument(
            call_id="call1",
            type="audio",
            status="ended",
            caller_user_id="u1",
            callee_user_id="u2",
            started_at=FIXED_NOW,
            answered_at=FIXED_NOW,
            ended_at=FIXED_NOW,
            duration_ms=0,
        ),
        reply_preview=None,
        reactions=[],
    )

    normalized_message = to_message_doc(message)

    assert normalized_message.type == "call"
    assert normalized_message.call is not None
    assert normalized_message.call.call_id == "call1"
    assert normalized_message.call.status == "ended"
