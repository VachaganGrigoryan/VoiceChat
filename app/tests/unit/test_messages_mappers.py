from __future__ import annotations

from datetime import UTC, datetime

from app.modules.messages.mappers import normalize_message_record, to_message_doc

FIXED_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)


def test_normalize_message_record_keeps_canonical_type_and_media_kind():
    message = {
        "_id": "m1",
        "conversation_id": "c1",
        "sender_id": "u1",
        "receiver_id": "u2",
        "type": "media",
        "text": None,
        "media": {
            "kind": "voice",
            "storage": "local",
            "key": "voice/test.mp3",
            "mime": "audio/mpeg",
            "size_bytes": 123,
            "duration_ms": 1000,
        },
        "status": "sent",
        "hidden_for_user_ids": [],
        "created_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }

    normalized_type, normalized_media = normalize_message_record(message)

    assert normalized_type == "media"
    assert normalized_media is not None
    assert normalized_media["kind"] == "voice"
    assert normalized_media["url"] == "/media/voice/test.mp3"


def test_normalize_message_record_defaults_file_kind_for_file_messages():
    message = {
        "_id": "m2",
        "conversation_id": "c1",
        "sender_id": "u1",
        "receiver_id": "u2",
        "type": "file",
        "text": None,
        "media": {
            "storage": "local",
            "key": "files/archive.zip",
            "mime": "application/zip",
            "size_bytes": 999,
            "duration_ms": None,
        },
        "status": "sent",
        "hidden_for_user_ids": [],
        "created_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }

    normalized_type, normalized_media = normalize_message_record(message)

    assert normalized_type == "file"
    assert normalized_media is not None
    assert normalized_media["kind"] == "file"
    assert normalized_media["url"] == "/media/files/archive.zip"


def test_to_message_doc_uses_canonical_reply_preview_shape():
    message = {
        "_id": "m3",
        "conversation_id": "c1",
        "sender_id": "u1",
        "receiver_id": "u2",
        "type": "media",
        "text": "listen",
        "media": {
            "kind": "audio",
            "storage": "local",
            "key": "audio/track.mp3",
            "mime": "audio/mpeg",
            "size_bytes": 321,
            "duration_ms": 5000,
        },
        "status": "sent",
        "edited_at": None,
        "delivered_at": None,
        "read_at": None,
        "reply_mode": "quote",
        "reply_to_message_id": "root1",
        "thread_root_id": None,
        "reply_preview": {
            "message_id": "root1",
            "sender_id": "u2",
            "type": "file",
            "media_kind": "file",
            "text": "archive",
            "is_deleted": False,
        },
        "is_thread_root": False,
        "thread_reply_count": 0,
        "last_thread_reply_at": None,
        "reactions": [],
        "hidden_for_user_ids": [],
        "created_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }

    normalized_message = to_message_doc(message)

    assert normalized_message.type == "media"
    assert normalized_message.media is not None
    assert normalized_message.media.kind == "audio"
    assert normalized_message.reply_preview is not None
    assert normalized_message.reply_preview.type == "file"
    assert normalized_message.reply_preview.media_kind == "file"
