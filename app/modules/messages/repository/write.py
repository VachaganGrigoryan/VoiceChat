from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.models import CallDocument, CallMessageDocument, MediaDocument, MessageDocument
from app.db.object_id import parse_object_id as _oid
from app.modules.messages.repository.helpers import (
    build_reply_preview,
    call_duration_ms,
    conversation_id_for,
)


class WriteRepositoryMixin:
    async def create_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        text: str | None = None,
        media: MediaDocument | None = None,
        call: CallMessageDocument | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> MessageDocument:
        conv_id = conversation_id_for(sender_id, receiver_id)
        created_ts = created_at or datetime.now(UTC)
        updated_ts = updated_at or created_ts

        message = MessageDocument(
            conversation_id=conv_id,
            sender_id=sender_id,
            receiver_id=receiver_id,
            type=message_type,
            text=text,
            media=media,
            call=call,
            created_at=created_ts,
            updated_at=updated_ts,
        )
        await message.insert()
        return message

    async def find_call_message_by_call_id(
        self, *, call_id: str
    ) -> MessageDocument | None:
        return await MessageDocument.find_one({"type": "call", "call.call_id": call_id})

    async def create_call_message(
        self,
        *,
        call_doc: CallDocument,
    ) -> MessageDocument:
        terminal_at = (
            call_doc.ended_at
            or call_doc.updated_at
            or call_doc.created_at
            or datetime.now(UTC)
        )
        payload = {
            "call_id": str(call_doc.id) if call_doc.id else "",
            "type": call_doc.type,
            "status": call_doc.status,
            "caller_user_id": call_doc.caller_user_id,
            "callee_user_id": call_doc.callee_user_id,
            "started_at": call_doc.created_at,
            "answered_at": call_doc.answered_at,
            "ended_at": call_doc.ended_at,
            "duration_ms": max(call_duration_ms(call_doc), 0),
        }

        try:
            return await self.create_message(
                sender_id=call_doc.caller_user_id,
                receiver_id=call_doc.callee_user_id,
                message_type="call",
                call=CallMessageDocument.model_validate(payload),
                created_at=terminal_at,
                updated_at=terminal_at,
            )
        except DuplicateKeyError:
            existing = await self.find_call_message_by_call_id(
                call_id=payload["call_id"]
            )
            if existing is not None:
                return existing
            raise

    async def create_quote_reply(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        reply_to_message_id: str,
        text: str | None = None,
        media: MediaDocument | None = None,
    ) -> MessageDocument:
        target = await self._load_reply_target(
            sender_id=sender_id,
            receiver_id=receiver_id,
            reply_to_message_id=reply_to_message_id,
        )
        doc = await self.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type=message_type,
            text=text,
            media=media,
        )

        return await doc.set(
            {
                "reply_mode": "quote",
                "reply_to_message_id": target.str_id,
                "reply_preview": build_reply_preview(target),
                "updated_at": datetime.now(UTC),
            }
        )

    async def create_thread_reply(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        reply_to_message_id: str,
        text: str | None = None,
        media: MediaDocument | None = None,
    ) -> MessageDocument:
        target = await self._load_reply_target(
            sender_id=sender_id,
            receiver_id=receiver_id,
            reply_to_message_id=reply_to_message_id,
        )
        thread_root_id = target.thread_root_id or target.str_id
        now = datetime.now(UTC)

        created = MessageDocument(
            conversation_id=target.conversation_id,
            sender_id=sender_id,
            receiver_id=receiver_id,
            type=message_type,
            text=text,
            media=media,
            reply_mode="thread",
            reply_to_message_id=target.str_id,
            thread_root_id=thread_root_id,
            reply_preview=build_reply_preview(target),
            created_at=now,
            updated_at=now,
        )
        await created.insert()

        root = await self.col.find_one_and_update(
            {
                "_id": _oid(thread_root_id),
                "conversation_id": target.conversation_id,
            },
            {
                "$set": {
                    "is_thread_root": True,
                    "last_thread_reply_at": now,
                    "updated_at": now,
                },
                "$inc": {"thread_reply_count": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if root is None:
            from app.core.errors import AppError

            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )

        return created

    async def create_voice_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        audio: MediaDocument,
    ) -> MessageDocument:
        media = audio.model_copy(update={"kind": "voice"})
        return await self.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type="media",
            media=media,
        )
