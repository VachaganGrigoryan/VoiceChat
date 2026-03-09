from __future__ import annotations

from typing import Optional

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import AppError
from app.db.mongo import get_db
from app.infra.storage import MediaStorageService
from app.modules.auth.repository import UsersRepository
from app.modules.messages.mappers import to_message_doc
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.schemas import ConversationItem, ConversationPeer, ConversationLastMessage
from app.modules.realtime.presence import get_presence_backend

ALLOWED_MIME = {
    "audio/mpeg",      # mp3
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",       # m4a often comes as audio/mp4
    "audio/aac",
    "audio/webm",
    "audio/ogg",
}


def _max_bytes() -> int:
    return int(settings.max_file_size_mb) * 1024 * 1024


class MessagesService:
    def __init__(self, repo: MessagesRepository):
        self.repo = repo

    async def upload_voice_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        file: UploadFile,
        duration_ms: Optional[int] = None,
    ):
        if not file or not file.filename:
            raise AppError(code="FILE_REQUIRED", message="Audio file is required", status_code=400)

        mime = (file.content_type or "").lower().strip()
        if mime not in ALLOWED_MIME:
            raise AppError(
                code="UNSUPPORTED_MEDIA_TYPE",
                message=f"Unsupported audio type: {mime or 'unknown'}",
                status_code=415,
                details={"allowed": sorted(ALLOWED_MIME)},
            )

        content = await file.read()
        if not content:
            raise AppError(code="EMPTY_FILE", message="Uploaded file is empty", status_code=400)

        if len(content) > _max_bytes():
            raise AppError(
                code="FILE_TOO_LARGE",
                message=f"File exceeds max size {settings.max_file_size_mb}MB",
                status_code=413,
            )

        storage = MediaStorageService()
        stored = await storage.save_voice(
            user_id=sender_id,
            filename=file.filename,
            content=content,
            mime=mime,
        )

        audio_meta = {
            "storage": stored.storage,
            "key": stored.key,
            "url": stored.url,
            "mime": stored.mime,
            "size_bytes": stored.size_bytes,
            "duration_ms": duration_ms,
        }

        doc = await self.repo.create_voice_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            audio=audio_meta,
        )

        return to_message_doc(doc)

    async def send_text_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        text: str,
    ):
        normalized = text.strip()
        if not normalized:
            raise AppError(
                code="TEXT_REQUIRED",
                message="Text message cannot be empty",
                status_code=400,
            )

        if len(normalized) > 4000:
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
