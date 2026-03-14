from __future__ import annotations

from typing import Optional, Protocol

from fastapi import UploadFile

from app.core.errors import AppError
from app.db.mongo import get_db
from app.infra.storage import get_storage, storage_key_builder, FolderKind
from app.modules.auth.repository import UsersRepository
from app.modules.messages.mappers import to_message_doc
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.schemas import ConversationItem, ConversationPeer, ConversationLastMessage
from app.modules.realtime.presence import get_presence_backend

ALLOWED_AUDIO_MIME = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",
    "audio/aac",
    "audio/webm",
    "audio/ogg",
}

ALLOWED_IMAGE_MIME = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

ALLOWED_STICKER_MIME = {
    "image/png",
    "image/webp",
}

ALLOWED_VIDEO_MIME = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
}

MAX_TEXT_LENGTH = 4000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_STICKER_BYTES = 2 * 1024 * 1024

MEDIA_RULES: dict[str, dict] = {
    "voice": {
        "allowed_mime": ALLOWED_AUDIO_MIME,
        "max_bytes": MAX_FILE_BYTES,
        "folder": "voice",
    },
    "image": {
        "allowed_mime": ALLOWED_IMAGE_MIME,
        "max_bytes": MAX_FILE_BYTES,
        "folder": "media",
    },
    "sticker": {
        "allowed_mime": ALLOWED_STICKER_MIME,
        "max_bytes": MAX_STICKER_BYTES,
        "folder": "media",
    },
    "video": {
        "allowed_mime": ALLOWED_VIDEO_MIME,
        "max_bytes": MAX_FILE_BYTES,
        "folder": "video",
    },
}


class PingsServiceProto(Protocol):
    async def ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None: ...



class MessagesService:
    def __init__(self, repo: MessagesRepository, pings_service: PingsServiceProto | None = None):
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

    def _build_media_meta(self, *, stored, duration_ms: int | None = None) -> dict:
        return {
            "storage": stored.storage,
            "key": stored.key,
            "url": stored.url,
            "mime": stored.mime,
            "size_bytes": stored.size_bytes,
            "duration_ms": duration_ms,
        }

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

    async def upload_media_message(
            self,
            *,
            sender_id: str,
            receiver_id: str,
            message_type: str,
            file: UploadFile,
            text: str | None = None,
            duration_ms: int | None = None,
    ):
        if self.pings_service is not None:
            await self.pings_service.ensure_can_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
            )

        rules = MEDIA_RULES.get(message_type)
        if not rules:
            raise AppError(
                code="UNSUPPORTED_MESSAGE_TYPE",
                message=f"Unsupported media message type: {message_type}",
                status_code=400,
            )

        stored = await self._store_media(
            sender_id=sender_id,
            file=file,
            allowed_mime=rules["allowed_mime"],
            max_bytes=rules["max_bytes"],
            folder=rules["folder"],
        )

        doc = await self.repo.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type=message_type,
            text=text.strip() if text else None,
            media=self._build_media_meta(stored=stored, duration_ms=duration_ms),
        )

        return to_message_doc(doc)

    async def send_text_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        text: str,
    ):
        if self.pings_service is not None:
            await self.pings_service.ensure_can_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
            )

        normalized = text.strip()
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

        doc = await self.repo.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type="text",
            text=normalized,
        )

        return to_message_doc(doc)

    async def get_history(
            self,
            *,
            user_id: str,
            peer_user_id: str,
            limit: int = 20,
            cursor: Optional[str] = None,
    ):
        docs, next_cursor = await self.repo.list_history(
            user_id=user_id,
            peer_user_id=peer_user_id,
            limit=limit,
            cursor=cursor,
        )
        items = [to_message_doc(d) for d in docs]
        return items, next_cursor

    async def list_conversations(
        self,
        *,
        user_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[ConversationItem], str | None]:
        rows, next_cursor = await self.repo.list_conversations_for_user(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )

        db = get_db()
        users_repo = UsersRepository(db)
        presence = get_presence_backend()

        items: list[ConversationItem] = []

        for row in rows:
            msg = row["last_message"]

            sender_id = str(msg["sender_id"])
            receiver_id = str(msg["receiver_id"])
            peer_user_id = receiver_id if sender_id == user_id else sender_id

            peer = await users_repo.find_by_id(peer_user_id)
            is_online = await presence.is_online(peer_user_id)

            media = msg.get("media")
            if media is None and msg.get("audio") is not None:
                media = msg.get("audio")

            text = msg.get("text")
            msg_type = msg.get("type") or msg.get("message_type") or "voice"

            items.append(
                ConversationItem(
                    conversation_id=row["_id"],
                    peer_user=ConversationPeer(
                        id=peer_user_id,
                        username=peer.get("username") if peer else None,
                        display_name=peer.get("display_name") if peer else None,
                        avatar=peer.get("avatar") if peer else None,
                        is_online=is_online,
                    ),
                    last_message=ConversationLastMessage(
                        id=str(msg["_id"]),
                        type=msg_type,
                        text=text,
                        media=media,
                        status=msg.get("status", "sent"),
                        created_at=msg["created_at"],
                    ),
                    last_message_at=msg["created_at"],
                )
            )

        return items, next_cursor
