from __future__ import annotations

from typing import Optional

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import AppError
from app.infra.storage import get_storage
from app.modules.messages.mappers import to_message_doc
from app.modules.messages.repository import MessagesRepository


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

        storage = get_storage()
        stored = await storage.save(filename=file.filename, content=content, mime=mime)

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
