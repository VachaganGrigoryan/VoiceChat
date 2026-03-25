from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.errors import AppError
from app.infra.storage.keys import FolderKind

UploadMessageType = Literal["media", "file"]
MediaRenderKind = Literal["voice", "audio", "image", "video", "file"]
PreviewMediaKind = Literal["voice", "audio", "image", "video"]

MAX_PREVIEW_MEDIA_BYTES = 10 * 1024 * 1024
MAX_GENERIC_FILE_BYTES = 25 * 1024 * 1024

AUDIO_MIME = frozenset(
    {
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/x-wav",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/aac",
        "audio/webm",
        "audio/ogg",
        "audio/amr",
        "audio/3gpp",
        "audio/flac",
    }
)

IMAGE_MIME = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)

VIDEO_MIME = frozenset(
    {
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "video/3gpp",
    }
)

DOCUMENT_MIME = frozenset(
    {
        "text/plain",
        "text/csv",
        "application/pdf",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

ARCHIVE_MIME = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-rar-compressed",
        "application/vnd.rar",
        "application/x-7z-compressed",
        "application/gzip",
        "application/x-gzip",
        "application/x-tar",
    }
)

GENERIC_FILE_MIME = frozenset(
    set(AUDIO_MIME)
    | set(IMAGE_MIME)
    | set(VIDEO_MIME)
    | set(DOCUMENT_MIME)
    | set(ARCHIVE_MIME)
)

PREVIEW_MEDIA_KINDS = frozenset({"voice", "audio", "image", "video"})


@dataclass(frozen=True)
class MediaPolicy:
    message_type: UploadMessageType
    media_kind: MediaRenderKind
    allowed_mime: frozenset[str]
    max_bytes: int
    folder: FolderKind


MEDIA_POLICIES: dict[PreviewMediaKind, MediaPolicy] = {
    "voice": MediaPolicy(
        message_type="media",
        media_kind="voice",
        allowed_mime=AUDIO_MIME,
        max_bytes=MAX_PREVIEW_MEDIA_BYTES,
        folder="voice",
    ),
    "audio": MediaPolicy(
        message_type="media",
        media_kind="audio",
        allowed_mime=AUDIO_MIME,
        max_bytes=MAX_PREVIEW_MEDIA_BYTES,
        folder="audio",
    ),
    "image": MediaPolicy(
        message_type="media",
        media_kind="image",
        allowed_mime=IMAGE_MIME,
        max_bytes=MAX_PREVIEW_MEDIA_BYTES,
        folder="media",
    ),
    "video": MediaPolicy(
        message_type="media",
        media_kind="video",
        allowed_mime=VIDEO_MIME,
        max_bytes=MAX_PREVIEW_MEDIA_BYTES,
        folder="video",
    ),
}

FILE_POLICY = MediaPolicy(
    message_type="file",
    media_kind="file",
    allowed_mime=GENERIC_FILE_MIME,
    max_bytes=MAX_GENERIC_FILE_BYTES,
    folder="files",
)


def resolve_media_policy(
    *,
    message_type: str,
    media_kind: str | None,
) -> MediaPolicy:
    if message_type == "file":
        if media_kind is not None:
            raise AppError(
                code="INVALID_MEDIA_KIND",
                message="media_kind must be omitted when type is file",
                status_code=400,
            )
        return FILE_POLICY

    if message_type != "media":
        raise AppError(
            code="UNSUPPORTED_MESSAGE_TYPE",
            message=f"Unsupported media message type: {message_type}",
            status_code=400,
        )

    if media_kind not in PREVIEW_MEDIA_KINDS:
        raise AppError(
            code="INVALID_MEDIA_KIND",
            message="media_kind must be one of: voice, audio, image, video",
            status_code=400,
        )

    return MEDIA_POLICIES[media_kind]
