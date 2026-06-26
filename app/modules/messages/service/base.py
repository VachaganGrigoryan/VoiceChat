from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from fastapi import UploadFile

from app.core.errors import AppError
from app.db.models import MediaDocument
from app.infra.storage import FolderKind, get_storage, storage_key_builder
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.schemas import MessageDoc, ReplyMode, ThreadSummary

EDIT_WINDOW_MINUTES = 15
MAX_TEXT_LENGTH = 4000


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


class PingsServiceProto(Protocol):
    async def ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None: ...
    async def get_contact_state(
        self, *, viewer_user_id: str, peer_user_id: str
    ) -> Any: ...
    async def get_contact_states(
        self, *, viewer_user_id: str, peer_user_ids: list[str]
    ) -> dict[str, Any]: ...
    async def delete_ping_for_pair(
        self, *, user_id: str, peer_user_id: str
    ) -> bool: ...


class BaseMessagesService:
    def __init__(
        self,
        repo: MessagesRepository,
        pings_service: PingsServiceProto | None = None,
    ):
        self.repo = repo
        self.pings_service = pings_service

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
