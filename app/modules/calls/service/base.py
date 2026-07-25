from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.core.errors import AppError
from app.db.models import CallDocument, MessageDocument, UserDocument
from app.modules.calls.repository import CallsRepository
from app.modules.calls.schemas import CallDoc, IceServer
from app.modules.calls.state import TERMINAL_CALL_STATUSES
from app.modules.messages.repository.mappers import to_message_doc
from app.modules.messages.schemas import MessageDoc


class PingsServiceProto(Protocol):
    async def ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None: ...


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> UserDocument | None: ...
    async def find_by_ids(self, user_ids: list[str]) -> dict[str, UserDocument]: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


class WebRTCServiceProto(Protocol):
    async def get_ice_servers(self) -> list[IceServer]: ...


class MessagesRepositoryProto(Protocol):
    async def create_call_message(
        self, *, call_doc: CallDocument, conversation_id: str
    ) -> MessageDocument: ...


class ConversationsServiceProto(Protocol):
    async def ensure_dm_conversation(
        self, *, user_id: str, peer_user_id: str
    ) -> Any: ...
    async def materialize_dm_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_id: str,
        message_type: str,
        preview_text: str | None,
        created_at: datetime,
    ) -> None: ...
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


@dataclass
class CallTerminalResult:
    call: CallDoc
    history_message: MessageDoc | None = None


class BaseCallsService:
    def __init__(
        self,
        *,
        repo: CallsRepository,
        users_repo: UsersRepositoryProto,
        pings_service: PingsServiceProto,
        presence_service: PresenceServiceProto | None = None,
        webrtc_service: WebRTCServiceProto | None = None,
        messages_repo: MessagesRepositoryProto | None = None,
        conversations_service: ConversationsServiceProto | None = None,
    ) -> None:
        self.repo = repo
        self.users_repo = users_repo
        self.pings_service = pings_service
        self.presence_service = presence_service
        self.webrtc_service = webrtc_service
        self.messages_repo = messages_repo
        self.conversations_service = conversations_service

    def _as_call_document(self, doc: CallDocument | dict[str, Any]) -> CallDocument:
        if isinstance(doc, CallDocument):
            return doc
        return CallDocument.model_validate(doc)

    async def expire_stale_calls(self) -> int:
        due_call_ids = await self.repo.list_due_call_ids()
        expired_count = 0
        for call_id in due_call_ids:
            result = await self._expire_call_if_due_with_history(call_id=call_id)
            if result is not None:
                expired_count += 1
        return expired_count

    async def expire_call_if_due(self, *, call_id: str) -> CallTerminalResult | None:
        return await self._expire_call_if_due_with_history(call_id=call_id)

    async def _get_participant_call(
        self, *, user_id: str, call_id: str
    ) -> CallDocument:
        await self._expire_call_if_due_with_history(call_id=call_id)
        call = await self.repo.find_by_id(call_id)
        if call is not None:
            call = self._as_call_document(call)
        if not call or user_id not in call.participant_user_ids:
            raise AppError(
                code="CALL_NOT_FOUND", message="Call not found", status_code=404
            )
        return call

    async def _reload_after_conflict(
        self, *, user_id: str, call_id: str
    ) -> CallDocument:
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)
        self._raise_if_expired(current)
        raise AppError(
            code="INVALID_CALL_STATE",
            message=f"Call is already {current.status}",
            status_code=409,
        )

    async def _expire_call_if_due_with_history(
        self,
        *,
        call_id: str,
    ) -> CallTerminalResult | None:
        expired = await self.repo.expire_call_if_due(call_id=call_id)
        if expired is None:
            return None
        return await self._build_terminal_result(self._as_call_document(expired))

    async def _build_terminal_result(
        self, call_doc: CallDocument
    ) -> CallTerminalResult:
        history_message = await self._ensure_history_message(call_doc=call_doc)
        return CallTerminalResult(
            call=self.to_call_doc(call_doc),
            history_message=history_message,
        )

    async def _ensure_history_message(
        self, *, call_doc: CallDocument
    ) -> MessageDoc | None:
        if call_doc.status not in TERMINAL_CALL_STATUSES:
            return None
        if self.messages_repo is None:
            return None

        conversation_id = None
        if self.conversations_service is not None:
            conversation = await self.conversations_service.ensure_dm_conversation(
                user_id=str(call_doc.caller_user_id),
                peer_user_id=str(call_doc.callee_user_id),
            )
            conversation_id = conversation.str_id

        history_message_doc = await self.messages_repo.create_call_message(
            call_doc=call_doc,
            conversation_id=conversation_id,
        )
        if not isinstance(history_message_doc, MessageDocument):
            history_message_doc = MessageDocument.model_validate(history_message_doc)
        history_message_id = history_message_doc.str_id
        if call_doc.history_message_id != history_message_id:
            updated = await self.repo.set_history_message_id(
                call_id=call_doc.str_id,
                history_message_id=history_message_id,
            )
            if updated is not None:
                call_doc = updated

            if self.conversations_service is not None:
                await self.conversations_service.materialize_conversation_message(
                    conversation_id=history_message_doc.container_id,
                    sender_id=str(history_message_doc.sender_id),
                    message_id=history_message_id,
                    message_type=history_message_doc.type,
                    preview_text=None,
                    created_at=history_message_doc.created_at,
                )

        return to_message_doc(history_message_doc)

    def _raise_if_expired(self, call_doc: CallDocument) -> None:
        if call_doc.status == "expired":
            raise AppError(
                code="CALL_EXPIRED",
                message="Call expired",
                status_code=409,
            )
