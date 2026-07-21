from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.db.document import EmbeddedBase
from app.db.object_id import StrId


class MediaDocument(EmbeddedBase):
    kind: Literal["voice", "audio", "image", "video", "file"]
    storage: Literal["local", "s3"]
    key: str
    mime: str
    size_bytes: int = Field(ge=0)
    duration_ms: int | None = Field(default=None, ge=0)


class CallMessageDocument(EmbeddedBase):
    call_id: str
    type: Literal["audio", "video"]
    status: Literal["rejected", "cancelled", "expired", "ended"]
    caller_user_id: StrId
    callee_user_id: StrId
    started_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)


class ReplyPreviewDocument(EmbeddedBase):
    message_id: str
    sender_id: StrId
    type: Literal["text", "media", "file", "call"]
    media_kind: Literal["voice", "audio", "image", "video", "file"] | None = None
    text: str | None = None
    is_deleted: bool = False


class PlaintextContentDocument(EmbeddedBase):
    """Cleartext message body, present when the envelope encryption mode is "none".

    Canonical cleartext body for messages whose encryption mode is ``none``.
    """

    text: str | None = None
    media: MediaDocument | None = None
    call: CallMessageDocument | None = None


class EncryptionEnvelopeDocument(EmbeddedBase):
    """Per-recipient encryption metadata, reserved for future E2EE.

    Unused while `MessageContentDocument.encryption == "none"`; no cryptography is
    performed against these fields in this change.
    """

    scheme: str | None = None
    sender_device_id: str | None = None
    recipient_key_ids: list[str] = Field(default_factory=list)


class MessageContentDocument(EmbeddedBase):
    """Encryption-ready message body envelope.

    When `encryption == "none"` the body lives in `plaintext` (today's behavior).
    When `encryption == "e2ee"` the body lives in `ciphertext`/`envelope`.
    """

    encryption: Literal["none", "e2ee"] = "none"
    type: Literal["text", "media", "file", "call", "system"] = "text"
    plaintext: PlaintextContentDocument | None = None
    ciphertext: str | None = None
    envelope: EncryptionEnvelopeDocument | None = None


class ConversationPreviewDocument(EmbeddedBase):
    """Denormalized last-message preview for inbox rendering.

    Server-rendered plaintext while conversations are `encryption == "none"`; a
    future E2EE change replaces this with client-rendered previews.
    """

    message_id: str
    sender_id: StrId
    type: Literal["text", "media", "file", "call", "system"]
    text: str | None = None
    created_at: datetime


class MessageReactionDocument(EmbeddedBase):
    emoji: str
    user_ids: list[StrId] = Field(default_factory=list)
    count: int = Field(ge=0)
    updated_at: datetime


class CallParticipantStateDocument(EmbeddedBase):
    role: Literal["caller", "callee"]
    join_state: Literal["waiting", "joined", "disconnected"] = "waiting"
    audio_enabled: bool = True
    video_enabled: bool = False
    updated_at: datetime
