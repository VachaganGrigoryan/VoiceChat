from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, UTC, timedelta
from typing import Optional, Protocol, Any

from fastapi import UploadFile

from app.core.errors import AppError
from app.db.mongo import get_db
from app.infra.storage import get_storage, storage_key_builder, FolderKind
from app.modules.auth.repository import UsersRepository
from app.modules.messages.mappers import (
    normalize_call_payload,
    normalize_message_record,
    to_message_doc,
    to_thread_summary,
)
from app.modules.messages.media_policy import resolve_media_policy
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.schemas import (
    ConversationItem,
    ConversationPeer,
    ConversationLastMessage,
    DeleteMessageResponse,
    MessageDeleteOutcome,
    MessageDoc,
    ThreadSummary,
    ReplyMode,
)
from app.modules.pings.schemas import ContactState
from app.modules.realtime.presence import get_presence_backend
from app.modules.users.avatar import build_user_avatar_payload

EDIT_WINDOW_MINUTES = 15
MAX_TEXT_LENGTH = 4000


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


class MessagesService:
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
    ) -> dict:
        return {
            "kind": media_kind,
            "storage": stored.storage,
            "key": stored.key,
            "url": stored.url,
            "mime": stored.mime,
            "size_bytes": stored.size_bytes,
            "duration_ms": duration_ms,
        }

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

    async def upload_media_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        media_kind: str | None,
        file: UploadFile,
        text: str | None = None,
        duration_ms: int | None = None,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        if self.pings_service is not None:
            await self.pings_service.ensure_can_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
            )

        policy = resolve_media_policy(
            message_type=message_type,
            media_kind=media_kind,
        )

        stored = await self._store_media(
            sender_id=sender_id,
            file=file,
            allowed_mime=set(policy.allowed_mime),
            max_bytes=policy.max_bytes,
            folder=policy.folder,
        )

        normalized_reply_mode, normalized_reply_to_message_id = (
            self._normalize_reply_fields(
                reply_mode=reply_mode,
                reply_to_message_id=reply_to_message_id,
            )
        )

        try:
            result = await self._create_outgoing_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
                message_type=message_type,
                text=self._normalize_optional_text(text),
                media=self._build_media_meta(
                    stored=stored,
                    media_kind=policy.media_kind,
                    duration_ms=self._normalize_duration_ms(duration_ms),
                ),
                reply_mode=normalized_reply_mode,
                reply_to_message_id=normalized_reply_to_message_id,
            )
        except Exception:
            await get_storage(stored.storage).delete(stored.key)
            raise
        return result

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

    async def send_text_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        if self.pings_service is not None:
            await self.pings_service.ensure_can_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
            )

        normalized_reply_mode, normalized_reply_to_message_id = (
            self._normalize_reply_fields(
                reply_mode=reply_mode,
                reply_to_message_id=reply_to_message_id,
            )
        )

        return await self._create_outgoing_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type="text",
            text=self._normalize_text(text),
            reply_mode=normalized_reply_mode,
            reply_to_message_id=normalized_reply_to_message_id,
        )

    async def _create_outgoing_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        text: str | None = None,
        media: dict[str, Any] | None = None,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        if reply_mode == "quote":
            doc = await self.repo.create_quote_reply(
                sender_id=sender_id,
                receiver_id=receiver_id,
                message_type=message_type,
                text=text,
                media=media,
                reply_to_message_id=reply_to_message_id,
            )
            return SendMessageResult(message=to_message_doc(doc))

        if reply_mode == "thread":
            doc = await self.repo.create_thread_reply(
                sender_id=sender_id,
                receiver_id=receiver_id,
                message_type=message_type,
                text=text,
                media=media,
                reply_to_message_id=reply_to_message_id,
            )
            summary_doc = await self.repo.load_thread_summary(
                message_id=doc["thread_root_id"],
                user_id=sender_id,
            )
            return SendMessageResult(
                message=to_message_doc(doc),
                thread_summary=to_thread_summary(summary_doc),
            )

        doc = await self.repo.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type=message_type,
            text=text,
            media=media,
        )
        return SendMessageResult(message=to_message_doc(doc))

    async def mark_delivered(self, *, message_id: str, receiver_id: str):
        doc = await self.repo.mark_delivered_for_receiver(
            message_id=message_id,
            receiver_id=receiver_id,
        )
        return to_message_doc(doc)

    async def mark_read(self, *, message_id: str, receiver_id: str):
        doc = await self.repo.mark_read_for_receiver(
            message_id=message_id,
            receiver_id=receiver_id,
        )
        return to_message_doc(doc)

    async def mark_conversation_read(
        self, *, receiver_id: str, peer_user_id: str
    ) -> int:
        return await self.repo.mark_conversation_read_for_receiver(
            receiver_id=receiver_id,
            peer_user_id=peer_user_id,
        )

    async def edit_text_message(self, *, message_id: str, sender_id: str, text: str):
        existing = await self.repo.get_by_id(message_id=message_id)
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        created_at = existing["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        if datetime.now(UTC) - created_at > timedelta(minutes=EDIT_WINDOW_MINUTES):
            raise AppError(
                code="EDIT_WINDOW_EXPIRED",
                message="Edit window expired",
                status_code=400,
            )

        doc = await self.repo.edit_text_message(
            message_id=message_id,
            sender_id=sender_id,
            text=self._normalize_text(text),
        )
        return to_message_doc(doc)

    async def delete_message(self, *, message_id: str, actor_user_id: str):
        existing = await self.repo.get_by_id(message_id=message_id)
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        owner_user_id = str(existing["sender_id"])
        receiver_user_id = str(existing["receiver_id"])
        if actor_user_id not in {owner_user_id, receiver_user_id}:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        if actor_user_id == owner_user_id:
            if existing.get("type") == "call":
                hidden = await self.repo.hide_message_for_user(
                    message_id=message_id,
                    user_id=actor_user_id,
                )
                return MessageDeleteOutcome(
                    response=DeleteMessageResponse(
                        message_id=message_id,
                        conversation_id=hidden["conversation_id"],
                        actor_user_id=actor_user_id,
                        deleted_for_everyone=False,
                        hidden_for_me=True,
                        deleted_media=False,
                    ),
                    sender_id=owner_user_id,
                    receiver_id=receiver_user_id,
                )

            deleted = await self.repo.hard_delete_owned_message(
                message_id=message_id,
                sender_id=actor_user_id,
            )

            media = deleted.get("media")
            deleted_media = False
            if media and media.get("key") and media.get("storage"):
                await get_storage(media["storage"]).delete(media["key"])
                deleted_media = True

            return MessageDeleteOutcome(
                response=DeleteMessageResponse(
                    message_id=message_id,
                    conversation_id=deleted["conversation_id"],
                    actor_user_id=actor_user_id,
                    deleted_for_everyone=True,
                    hidden_for_me=False,
                    deleted_media=deleted_media,
                ),
                sender_id=owner_user_id,
                receiver_id=receiver_user_id,
            )

        hidden = await self.repo.hide_message_for_user(
            message_id=message_id,
            user_id=actor_user_id,
        )
        return MessageDeleteOutcome(
            response=DeleteMessageResponse(
                message_id=message_id,
                conversation_id=hidden["conversation_id"],
                actor_user_id=actor_user_id,
                deleted_for_everyone=False,
                hidden_for_me=True,
                deleted_media=False,
            ),
            sender_id=owner_user_id,
            receiver_id=receiver_user_id,
        )

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

    async def get_thread(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> list[MessageDoc]:
        docs = await self.repo.load_thread_messages(
            message_id=message_id,
            user_id=user_id,
        )
        return [to_message_doc(doc) for doc in docs]

    async def get_thread_summary(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> ThreadSummary:
        doc = await self.repo.load_thread_summary(
            message_id=message_id,
            user_id=user_id,
        )
        return to_thread_summary(doc)

    async def add_reaction(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDoc:
        doc = await self.repo.add_or_toggle_grouped_reaction(
            message_id=message_id,
            user_id=user_id,
            emoji=self._normalize_emoji(emoji),
        )
        return to_message_doc(doc)

    async def remove_reaction(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDoc:
        doc = await self.repo.remove_grouped_reaction(
            message_id=message_id,
            user_id=user_id,
            emoji=self._normalize_emoji(emoji),
        )
        return to_message_doc(doc)

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
        peer_user_ids = list(
            dict.fromkeys(
                (
                    str(row["last_message"]["receiver_id"])
                    if str(row["last_message"]["sender_id"]) == user_id
                    else str(row["last_message"]["sender_id"])
                )
                for row in rows
            )
        )

        users_task = asyncio.create_task(users_repo.find_by_ids(peer_user_ids))
        presence_task = asyncio.create_task(
            self._get_presence_map(presence=presence, user_ids=peer_user_ids)
        )
        contact_states_task = asyncio.create_task(
            self._get_contact_states(user_id=user_id, peer_user_ids=peer_user_ids)
        )
        users_by_id, online_by_id, contact_states = await asyncio.gather(
            users_task,
            presence_task,
            contact_states_task,
        )

        for row in rows:
            msg = row["last_message"]

            sender_id = str(msg["sender_id"])
            receiver_id = str(msg["receiver_id"])
            peer_user_id = receiver_id if sender_id == user_id else sender_id

            peer = users_by_id.get(peer_user_id)
            is_online = online_by_id.get(peer_user_id, False)
            contact_state = contact_states.get(
                peer_user_id,
                ContactState(can_ping=True, chat_allowed=False, ping_status="none"),
            )

            avatar = build_user_avatar_payload(peer.get("avatar")) if peer else None

            text = msg.get("text")
            msg_type, media = normalize_message_record(msg)
            call = normalize_call_payload(message_type=msg_type, call=msg.get("call"))

            items.append(
                ConversationItem(
                    conversation_id=row["_id"],
                    peer_user=ConversationPeer(
                        id=peer_user_id,
                        username=peer.get("username") if peer else None,
                        display_name=peer.get("display_name") if peer else None,
                        avatar=avatar,
                        is_online=is_online,
                        can_ping=contact_state.can_ping,
                        chat_allowed=contact_state.chat_allowed,
                        ping_status=contact_state.ping_status,
                    ),
                    last_message=ConversationLastMessage(
                        id=str(msg["_id"]),
                        type=msg_type,
                        text=text,
                        media=media,
                        call=call,
                        status=msg.get("status", "sent"),
                        created_at=msg["created_at"],
                    ),
                    last_message_at=msg["created_at"],
                    unread_count=row.get("unread_count", 0),
                )
            )

        return items, next_cursor

    async def _get_presence_map(
        self, *, presence, user_ids: list[str]
    ) -> dict[str, bool]:
        if not user_ids:
            return {}

        statuses = await asyncio.gather(
            *(presence.is_online(user_id) for user_id in user_ids)
        )
        return dict(zip(user_ids, statuses))

    async def _get_contact_states(
        self, *, user_id: str, peer_user_ids: list[str]
    ) -> dict[str, ContactState]:
        if self.pings_service is None or not peer_user_ids:
            return {}
        return await self.pings_service.get_contact_states(
            viewer_user_id=user_id,
            peer_user_ids=peer_user_ids,
        )
