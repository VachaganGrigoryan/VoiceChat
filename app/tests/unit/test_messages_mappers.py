from __future__ import annotations

from datetime import UTC, datetime

from app.modules.messages.mappers import normalize_message_record, to_message_doc

FIXED_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)


def test_normalize_message_record_maps_legacy_types_to_new_shape():
    legacy_voice = {
        "_id": "m1",
        "conversation_id": "c1",
        "sender_id": "u1",
        "receiver_id": "u2",
        "type": "voice",
        "text": None,
        "media": {
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
    legacy_sticker = {
        **legacy_voice,
        "_id": "m2",
        "type": "sticker",
        "media": {
            "storage": "local",
            "key": "media/sticker.webp",
            "mime": "image/webp",
            "size_bytes": 42,
            "duration_ms": None,
        },
    }
    legacy_emoji = {
        **legacy_voice,
        "_id": "m3",
        "type": "emoji",
        "text": "🔥",
        "media": None,
    }

    voice_type, voice_media = normalize_message_record(legacy_voice)
    sticker_type, sticker_media = normalize_message_record(legacy_sticker)
    emoji_type, emoji_media = normalize_message_record(legacy_emoji)

    assert voice_type == "media"
    assert voice_media["kind"] == "voice"
    assert sticker_type == "media"
    assert sticker_media["kind"] == "image"
    assert emoji_type == "text"
    assert emoji_media is None


def test_to_message_doc_infers_media_kind_for_new_media_and_file_messages():
    media_doc = {
        "_id": "m1",
        "conversation_id": "c1",
        "sender_id": "u1",
        "receiver_id": "u2",
        "type": "media",
        "text": "listen",
        "media": {
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
        "reply_mode": None,
        "reply_to_message_id": None,
        "thread_root_id": None,
        "reply_preview": None,
        "is_thread_root": False,
        "thread_reply_count": 0,
        "last_thread_reply_at": None,
        "reactions": [],
        "hidden_for_user_ids": [],
        "created_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }
    file_doc = {
        **media_doc,
        "_id": "m2",
        "type": "file",
        "media": {
            "storage": "local",
            "key": "files/archive.zip",
            "mime": "application/zip",
            "size_bytes": 999,
            "duration_ms": None,
        },
    }

    normalized_media = to_message_doc(media_doc)
    normalized_file = to_message_doc(file_doc)

    assert normalized_media.type == "media"
    assert normalized_media.media is not None
    assert normalized_media.media.kind == "audio"
    assert normalized_file.type == "file"
    assert normalized_file.media is not None
    assert normalized_file.media.kind == "file"
