from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.modules.messages.media_policy import resolve_media_policy


def test_media_policy_resolves_voice_and_audio_to_same_mime_family():
    voice_policy = resolve_media_policy(message_type="media", media_kind="voice")
    audio_policy = resolve_media_policy(message_type="media", media_kind="audio")

    assert voice_policy.message_type == "media"
    assert voice_policy.media_kind == "voice"
    assert audio_policy.media_kind == "audio"
    assert voice_policy.allowed_mime == audio_policy.allowed_mime
    assert voice_policy.folder == "voice"
    assert audio_policy.folder == "audio"


def test_file_policy_accepts_documents_archives_and_previewable_mimes():
    policy = resolve_media_policy(message_type="file", media_kind=None)

    assert policy.message_type == "file"
    assert policy.media_kind == "file"
    assert policy.folder == "files"
    assert "application/pdf" in policy.allowed_mime
    assert "application/zip" in policy.allowed_mime
    assert "audio/mpeg" in policy.allowed_mime
    assert "image/png" in policy.allowed_mime
    assert "video/mp4" in policy.allowed_mime


def test_media_policy_rejects_invalid_type_and_media_kind_combinations():
    with pytest.raises(AppError) as invalid_type:
        resolve_media_policy(message_type="voice", media_kind="voice")
    assert invalid_type.value.code == "UNSUPPORTED_MESSAGE_TYPE"

    with pytest.raises(AppError) as missing_kind:
        resolve_media_policy(message_type="media", media_kind=None)
    assert missing_kind.value.code == "INVALID_MEDIA_KIND"

    with pytest.raises(AppError) as extra_kind:
        resolve_media_policy(message_type="file", media_kind="image")
    assert extra_kind.value.code == "INVALID_MEDIA_KIND"
