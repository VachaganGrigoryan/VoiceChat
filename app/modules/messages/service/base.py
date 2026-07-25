from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Optional, Protocol

from fastapi import UploadFile

from app.core.errors import AppError
from app.db.models import MediaDocument, MessageContainerType
from app.infra.storage import FolderKind, get_storage, storage_key_builder
from app.modules.authorization import AuthorizationService
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.schemas import MessageDoc, ReplyMode, ThreadSummary

EDIT_WINDOW_MINUTES = 15
MAX_TEXT_LENGTH = 4000
MENTION_PATTERN = re.compile(r"(?<![\w])@([A-Za-z0-9_][A-Za-z0-9_.-]{1,63}|all|here)\b")


def _media_dict(media: Any) -> dict[str, Any] | None:
    if media is None:
        return None
    if isinstance(media, dict):
        return media
    if hasattr(media, "model_dump"):
        return media.model_dump()
    return None


@dataclass
class SendMessageResult:
    message: MessageDoc
    thread_summary: ThreadSummary | None = None


@dataclass
class ReleasedScheduledMessage:
    """A scheduled message that has just been released, with its fan-out targets."""

    result: SendMessageResult
    participant_ids: list[str]


class PingsServiceProto(Protocol):
    async def delete_ping_for_pair(
        self, *, user_id: str, peer_user_id: str
    ) -> bool: ...


class ConversationsServiceProto(Protocol):
    async def materialize_conversation_message(
        self,
        *,
        conversation_id: str,
        sender_id: str,
        message_id: str,
        message_type: str,
        preview_text: str | None,
        created_at: datetime,
    ) -> None: ...

    async def mention_targets_for_conversation(
        self, *, conversation_id: str
    ) -> dict[str, str]: ...

    async def accessible_conversation_ids(self, *, user_id: str) -> list[str]: ...

    async def conversation_participant_ids(
        self, *, conversation_id: str
    ) -> list[str]: ...


class BaseMessagesService:
    def __init__(
        self,
        repo: MessagesRepository,
        pings_service: PingsServiceProto | None = None,
        conversations_service: ConversationsServiceProto | None = None,
        authorization: AuthorizationService | None = None,
    ):
        self.repo = repo
        self.pings_service = pings_service
        self.conversations_service = conversations_service
        # A message inherits authorization from its container (§91): every
        # message action is decided against the container resource, never
        # against the message itself.
        self.authorization = authorization or AuthorizationService()

    async def _require_container_permission(
        self,
        *,
        user_id: str,
        action: str,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str | None = None,
        message: str | None = None,
    ) -> None:
        """Authorize a message action against its container resource (§91).

        ``sender_id`` is the message's author, which is what resolves the
        ``*.own`` actions — a sender always acts on their own message.
        """
        await self.authorization.require(
            user_id,
            action,
            container_type,
            container_id,
            sender_id=sender_id,
            message=message,
        )

    async def _read_upload(self, *, file: UploadFile) -> bytes:
        if not file or not file.filename:
            raise AppError(
                code="FILE_REQUIRED",
                message="File is required",
                status_code=400,
            )

        content = await file.read()
        if not content:
            raise AppError(
                code="EMPTY_FILE",
                message="Uploaded file is empty",
                status_code=400,
            )

        return content

    def _build_media_meta(
        self,
        *,
        stored,
        media_kind: str,
        duration_ms: int | None = None,
    ) -> MediaDocument:
        return MediaDocument(
            kind=media_kind,
            storage=stored.storage,
            key=stored.key,
            mime=stored.mime,
            size_bytes=stored.size_bytes,
            duration_ms=duration_ms,
        )

    def _normalize_optional_text(self, text: Optional[str] = None) -> Optional[str]:
        normalized = (text or "").strip()
        if not normalized:
            return None
        if len(normalized) > MAX_TEXT_LENGTH:
            raise AppError(
                code="TEXT_TOO_LONG",
                message="Text message is too long",
                status_code=400,
            )
        return normalized

    def _normalize_text(self, text: Optional[str] = None) -> str:
        normalized = (text or "").strip()
        if not normalized:
            raise AppError(
                code="TEXT_REQUIRED",
                message="Text message cannot be empty",
                status_code=400,
            )
        if len(normalized) > MAX_TEXT_LENGTH:
            raise AppError(
                code="TEXT_TOO_LONG",
                message="Text message is too long",
                status_code=400,
            )
        return normalized

    def _normalize_duration_ms(self, duration_ms: int | None) -> int | None:
        if duration_ms is None:
            return None
        if duration_ms < 0:
            raise AppError(
                code="INVALID_DURATION_MS",
                message="duration_ms must be greater than or equal to 0",
                status_code=400,
            )
        return duration_ms

    def _normalize_reply_fields(
        self,
        *,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> tuple[ReplyMode | None, str | None]:
        normalized_reply_to_message_id = (reply_to_message_id or "").strip() or None
        if reply_mode is None and normalized_reply_to_message_id is None:
            return None, None
        if reply_mode is None or normalized_reply_to_message_id is None:
            raise AppError(
                code="INVALID_REPLY_FIELDS",
                message="reply_mode and reply_to_message_id must be provided together",
                status_code=400,
            )
        return reply_mode, normalized_reply_to_message_id

    async def _resolve_mentions(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        text: str | None,
    ) -> tuple[list[str], str | None]:
        # Mention targets come from the conversation roster; a channel resolves
        # its audience through follows, which `channels-and-profile-feed` adds.
        if (
            not text
            or container_type != "conversation"
            or self.conversations_service is None
        ):
            return [], None

        mention_user_ids: list[str] = []
        mention_scope: str | None = None
        targets = await self.conversations_service.mention_targets_for_conversation(
            conversation_id=container_id
        )
        for match in MENTION_PATTERN.finditer(text):
            handle = match.group(1).lower()
            if handle in {"all", "here"}:
                mention_scope = handle
                continue

            user_id = targets.get(handle)
            if user_id is not None and user_id not in mention_user_ids:
                mention_user_ids.append(user_id)

        return mention_user_ids, mention_scope

    def _normalize_emoji(self, emoji: str) -> str:
        normalized = (emoji or "").strip()
        if not normalized:
            raise AppError(
                code="INVALID_EMOJI", message="emoji is required", status_code=400
            )
        return normalized

    async def _store_media(
        self,
        *,
        sender_id: str,
        file: UploadFile,
        allowed_mime: set[str],
        max_bytes: int,
        folder: FolderKind,
    ):
        mime = (file.content_type or "").lower().strip()
        if mime not in allowed_mime:
            raise AppError(
                code="UNSUPPORTED_MEDIA_TYPE",
                message=f"Unsupported file type: {mime or 'unknown'}",
                status_code=415,
                details={"allowed": sorted(allowed_mime)},
            )

        content = await self._read_upload(file=file)
        if len(content) > max_bytes:
            raise AppError(
                code="FILE_TOO_LARGE",
                message=f"File exceeds max size {max_bytes // (1024 * 1024)}MB",
                status_code=413,
            )

        storage = get_storage()
        key_builder = storage_key_builder(folder)

        return await storage.save(
            filename=file.filename,
            content=content,
            mime=mime,
            key=key_builder(sender_id, file.filename),
        )
