from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.db.models import (
    CallDocument,
    CallMessageDocument,
    ForwardedFromDocument,
    MediaDocument,
    MessageContainerType,
    MessageContentDocument,
    MessageDocument,
    PlaintextContentDocument,
)
from app.db.object_id import parse_object_id as _oid
from app.modules.messages.repository.helpers import (
    build_reply_preview,
    call_duration_ms,
)


class WriteRepositoryMixin:
    async def create_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
        message_type: str,
        text: str | None = None,
        media: MediaDocument | None = None,
        call: CallMessageDocument | None = None,
        plaintext: PlaintextContentDocument | None = None,
        attachments: list[MediaDocument] | None = None,
        mention_user_ids: list[str] | None = None,
        mention_scope: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> MessageDocument:
        created_ts = created_at or datetime.now(UTC)
        updated_ts = updated_at or created_ts
        content = MessageContentDocument(
            encryption="none",
            type=message_type,
            plaintext=plaintext
            or PlaintextContentDocument(text=text, media=media, call=call),
            attachments=attachments or [],
        )
        message = MessageDocument(
            container_type=container_type,
            container_id=container_id,
            sender_id=sender_id,
            type=message_type,
            content=content,
            mention_user_ids=mention_user_ids or [],
            mention_scope=mention_scope,
            created_at=created_ts,
            updated_at=updated_ts,
        )
        await message.insert()
        return message

    async def find_call_message_by_call_id(
        self, *, call_id: str
    ) -> MessageDocument | None:
        return await MessageDocument.find_one(
            {"type": "call", "content.plaintext.call.call_id": call_id}
        )

    async def create_call_message(
        self,
        *,
        call_doc: CallDocument,
        conversation_id: str,
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
                container_type="conversation",
                container_id=conversation_id,
                sender_id=call_doc.caller_user_id,
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

    async def create_forwarded_message(
        self,
        *,
        source: MessageDocument,
        target_container_type: MessageContainerType,
        target_container_id: str,
        sender_id: str,
    ) -> MessageDocument:
        """Copy ``source``'s content into the target container preserving a
        ``forwarded_from`` origin header referencing the original message."""
        now = datetime.now(UTC)
        content = (
            source.content.model_copy(deep=True)
            if source.content is not None
            else MessageContentDocument(encryption="none", type=source.type)
        )
        message = MessageDocument(
            container_type=target_container_type,
            container_id=target_container_id,
            sender_id=sender_id,
            type=source.type,
            content=content,
            forwarded_from=ForwardedFromDocument(
                conversation_id=str(source.container_id),
                message_id=source.str_id,
                sender_id=str(source.sender_id),
                forwarded_at=now,
            ),
            created_at=now,
            updated_at=now,
        )
        await message.insert()
        return message

    async def create_scheduled_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
        text: str,
        scheduled_for: datetime,
    ) -> MessageDocument:
        now = datetime.now(UTC)
        message = MessageDocument(
            container_type=container_type,
            container_id=container_id,
            sender_id=sender_id,
            type="text",
            content=MessageContentDocument(
                encryption="none",
                type="text",
                plaintext=PlaintextContentDocument(text=text),
            ),
            scheduled_for=scheduled_for,
            state="scheduled",
            created_at=now,
            updated_at=now,
        )
        await message.insert()
        return message

    async def list_scheduled_for_sender(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
    ) -> list[MessageDocument]:
        raw = (
            await self.col.find(
                {
                    "container_type": container_type,
                    "container_id": container_id,
                    "sender_id": sender_id,
                    "state": "scheduled",
                }
            )
            .sort([("scheduled_for", 1)])
            .to_list(length=None)
        )
        return [MessageDocument.model_validate(item) for item in raw]

    async def cancel_scheduled_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        sender_id: str,
    ) -> bool:
        result = await self.col.delete_one(
            {
                "_id": _oid(message_id),
                "container_type": container_type,
                "container_id": container_id,
                "sender_id": sender_id,
                "state": "scheduled",
            }
        )
        return result.deleted_count > 0

    async def claim_due_scheduled_messages(
        self, *, now: datetime, limit: int = 100
    ) -> list[MessageDocument]:
        """Flip due scheduled messages to ``sent`` and return the released docs.

        Each message is claimed with an atomic conditional update so concurrent
        workers never release the same message twice.
        """
        due = (
            await self.col.find({"state": "scheduled", "scheduled_for": {"$lte": now}})
            .sort([("scheduled_for", 1)])
            .limit(limit)
            .to_list(length=limit)
        )

        released: list[MessageDocument] = []
        for item in due:
            updated = await self.col.find_one_and_update(
                {"_id": item["_id"], "state": "scheduled"},
                {
                    "$set": {
                        "state": "sent",
                        "created_at": now,
                        "updated_at": now,
                        "scheduled_for": None,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated is not None:
                released.append(MessageDocument.model_validate(updated))
        return released

    async def create_quote_reply(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
        message_type: str,
        reply_to_message_id: str,
        text: str | None = None,
        media: MediaDocument | None = None,
        plaintext: PlaintextContentDocument | None = None,
        attachments: list[MediaDocument] | None = None,
        mention_user_ids: list[str] | None = None,
        mention_scope: str | None = None,
    ) -> MessageDocument:
        target = await self._load_reply_target_in_container(
            container_type=container_type,
            container_id=container_id,
            reply_to_message_id=reply_to_message_id,
        )
        doc = await self.create_message(
            container_type=container_type,
            container_id=container_id,
            sender_id=sender_id,
            message_type=message_type,
            text=text,
            media=media,
            plaintext=plaintext,
            attachments=attachments,
            mention_user_ids=mention_user_ids,
            mention_scope=mention_scope,
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
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
        message_type: str,
        reply_to_message_id: str,
        text: str | None = None,
        media: MediaDocument | None = None,
        plaintext: PlaintextContentDocument | None = None,
        attachments: list[MediaDocument] | None = None,
        mention_user_ids: list[str] | None = None,
        mention_scope: str | None = None,
    ) -> MessageDocument:
        """Create a thread reply (a channel container makes it a Comment, §35).

        The reply is stored flat: it shares its root's container and carries
        `thread_root_id`, with `reply_to_message_id` pointing at the specific
        parent for UI nesting. No thread document is created (§37).
        """
        target = await self._load_reply_target_in_container(
            container_type=container_type,
            container_id=container_id,
            reply_to_message_id=reply_to_message_id,
        )
        root = await self._load_thread_root_in_container(
            container_type=container_type,
            container_id=container_id,
            thread_root_id=target.thread_root_id or target.str_id,
        )
        now = datetime.now(UTC)
        created = MessageDocument(
            # A thread item shares its root's container by construction (§36).
            container_type=root.container_type,
            container_id=root.container_id,
            sender_id=sender_id,
            type=message_type,
            content=MessageContentDocument(
                encryption="none",
                type=message_type,
                plaintext=plaintext or PlaintextContentDocument(text=text, media=media),
                attachments=attachments or [],
            ),
            reply_mode="thread",
            reply_to_message_id=target.str_id,
            thread_root_id=root.str_id,
            reply_preview=build_reply_preview(target),
            mention_user_ids=mention_user_ids or [],
            mention_scope=mention_scope,
            created_at=now,
            updated_at=now,
        )
        await created.insert()

        bumped = await self.col.find_one_and_update(
            {
                "_id": _oid(root.str_id),
                "container_type": root.container_type,
                "container_id": root.container_id,
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
        if bumped is None:
            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )
        return created
