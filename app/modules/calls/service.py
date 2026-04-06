from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import AppError
from app.modules.calls.repository import CallsRepository
from app.modules.calls.schemas import (
    CallDirection,
    CallDoc,
    CallHistoryItem,
    CallPeerUserSummary,
    CallSession,
    CallStatus,
    CallType,
    IceServer,
)
from app.modules.calls.state import (
    LIVE_CALL_STATUSES,
    RECOVERABLE_CALL_STATUSES,
    TERMINAL_CALL_STATUSES,
    ensure_status_in,
)
from app.modules.messages.mappers import to_message_doc
from app.modules.messages.schemas import MessageDoc
from app.modules.users.avatar import build_user_avatar_payload


class PingsServiceProto(Protocol):
    async def ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None: ...


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> dict[str, Any] | None: ...
    async def find_by_ids(self, user_ids: list[str]) -> dict[str, dict[str, Any]]: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


class WebRTCServiceProto(Protocol):
    async def get_ice_servers(self) -> list[IceServer]: ...


class MessagesRepositoryProto(Protocol):
    async def create_call_message(self, *, call_doc: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class CallTerminalResult:
    call: CallDoc
    history_message: MessageDoc | None = None


class CallsService:
    def __init__(
        self,
        *,
        repo: CallsRepository,
        users_repo: UsersRepositoryProto,
        pings_service: PingsServiceProto,
        presence_service: PresenceServiceProto | None = None,
        webrtc_service: WebRTCServiceProto | None = None,
        messages_repo: MessagesRepositoryProto | None = None,
    ) -> None:
        self.repo = repo
        self.users_repo = users_repo
        self.pings_service = pings_service
        self.presence_service = presence_service
        self.webrtc_service = webrtc_service
        self.messages_repo = messages_repo

    async def expire_stale_calls(self) -> int:
        due_call_ids = await self.repo.list_due_call_ids()
        expired_count = 0
        for call_id in due_call_ids:
            result = await self._expire_call_if_due_with_history(call_id=call_id)
            if result is not None:
                expired_count += 1
        return expired_count

    async def get_active_call(self, *, user_id: str) -> dict[str, Any] | None:
        await self.expire_stale_calls()
        return await self.repo.find_live_call_for_user(
            user_id=user_id,
            statuses=RECOVERABLE_CALL_STATUSES,
        )

    async def get_participant_call(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.expire_stale_calls()
        return await self._get_participant_call(user_id=user_id, call_id=call_id)

    async def get_active_call_session(self, *, user_id: str) -> CallSession | None:
        call_doc = await self.get_active_call(user_id=user_id)
        if call_doc is None:
            return None

        return await self.build_session(
            call_doc=call_doc,
            viewer_user_id=user_id,
            include_ice_servers=True,
        )

    async def list_history(
        self,
        *,
        user_id: str,
        peer_user_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[CallHistoryItem], str | None]:
        await self.expire_stale_calls()
        docs, next_cursor = await self.repo.list_history(
            user_id=user_id,
            peer_user_id=peer_user_id,
            limit=limit,
            cursor=cursor,
        )

        peer_user_ids = list(
            dict.fromkeys(
                self._peer_user_id_from_doc(call_doc=doc, viewer_user_id=user_id)
                for doc in docs
            )
        )
        users_task = asyncio.create_task(self.users_repo.find_by_ids(peer_user_ids))
        presence_task = asyncio.create_task(self._get_presence_map(user_ids=peer_user_ids))
        users_by_id, online_by_id = await asyncio.gather(users_task, presence_task)

        items = [
            self._to_history_item(
                call_doc=doc,
                viewer_user_id=user_id,
                users_by_id=users_by_id,
                online_by_id=online_by_id,
            )
            for doc in docs
        ]
        return items, next_cursor

    async def create_call(
        self,
        *,
        caller_user_id: str,
        callee_user_id: str,
        call_type: CallType,
    ) -> dict[str, Any]:
        await self.expire_stale_calls()

        if caller_user_id == callee_user_id:
            raise AppError(
                code="INVALID_CALL_TARGET",
                message="Cannot call yourself",
                status_code=400,
            )

        target = await self.users_repo.find_by_id(callee_user_id)
        if not target:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        await self.pings_service.ensure_can_message(
            sender_id=caller_user_id,
            receiver_id=callee_user_id,
        )

        expires_at = datetime.now(UTC) + timedelta(seconds=settings.call_ring_timeout_seconds)
        return await self.repo.create_call(
            caller_user_id=caller_user_id,
            callee_user_id=callee_user_id,
            call_type=call_type,
            expires_at=expires_at,
        )

    async def accept_call(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        if user_id != current["callee_user_id"]:
            raise AppError(
                code="CALL_FORBIDDEN",
                message="Only the callee can accept this call",
                status_code=403,
            )

        self._raise_if_expired(current)
        ensure_status_in(current["status"], allowed_statuses=("ringing",))

        updated = await self.repo.accept_call(call_id=call_id, callee_user_id=user_id)
        if updated is not None:
            return updated
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def reject_call(self, *, user_id: str, call_id: str) -> CallTerminalResult:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        if user_id != current["callee_user_id"]:
            raise AppError(
                code="CALL_FORBIDDEN",
                message="Only the callee can reject this call",
                status_code=403,
            )

        self._raise_if_expired(current)
        ensure_status_in(current["status"], allowed_statuses=("ringing",))

        updated = await self.repo.reject_call(call_id=call_id, callee_user_id=user_id)
        if updated is not None:
            return await self._build_terminal_result(updated)
        await self._reload_after_conflict(user_id=user_id, call_id=call_id)
        raise AssertionError("unreachable")

    async def end_call(self, *, user_id: str, call_id: str) -> CallTerminalResult:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        status = current["status"]

        if status == "ringing":
            if user_id == current["caller_user_id"]:
                updated = await self.repo.cancel_call(call_id=call_id, caller_user_id=user_id)
            elif user_id == current["callee_user_id"]:
                updated = await self.repo.reject_call(call_id=call_id, callee_user_id=user_id)
            else:
                updated = None
        elif status in {"accepted", "connecting", "active", "reconnecting"}:
            updated = await self.repo.end_call(call_id=call_id, participant_user_id=user_id)
        else:
            updated = None

        if updated is not None:
            return await self._build_terminal_result(updated)
        await self._reload_after_conflict(user_id=user_id, call_id=call_id)
        raise AssertionError("unreachable")

    async def start_connecting(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        if user_id != current["caller_user_id"]:
            raise AppError(
                code="CALL_FORBIDDEN",
                message="Only the caller can send an offer",
                status_code=403,
            )

        self._raise_if_expired(current)
        ensure_status_in(current["status"], allowed_statuses=("accepted", "reconnecting"))

        updated = await self.repo.set_connecting(call_id=call_id, caller_user_id=user_id)
        if updated is not None:
            return updated
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def ensure_answer_allowed(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        if user_id != current["callee_user_id"]:
            raise AppError(
                code="CALL_FORBIDDEN",
                message="Only the callee can send an answer",
                status_code=403,
            )

        self._raise_if_expired(current)
        ensure_status_in(current["status"], allowed_statuses=("connecting",))
        return current

    async def ensure_ice_candidate_allowed(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        ensure_status_in(current["status"], allowed_statuses=("connecting", "active"))
        return current

    async def mark_active(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        if current["status"] == "active":
            return current

        ensure_status_in(current["status"], allowed_statuses=("connecting",))

        updated = await self.repo.set_active(call_id=call_id, participant_user_id=user_id)
        if updated is not None:
            return updated
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def mark_reconnecting_from_disconnect(
        self,
        *,
        user_id: str,
        call_id: str,
    ) -> dict[str, Any] | None:
        await self.expire_stale_calls()

        current = await self.repo.find_by_id(call_id)
        if not current or user_id not in current.get("participant_user_ids", []):
            return None

        self._raise_if_expired(current)
        if current["status"] not in RECOVERABLE_CALL_STATUSES:
            return None

        deadline = datetime.now(UTC) + timedelta(seconds=settings.call_reconnect_grace_seconds)
        updated = await self.repo.mark_reconnecting(
            call_id=call_id,
            participant_user_id=user_id,
            reconnect_deadline_at=deadline,
        )
        if updated is not None:
            return updated

        refreshed = await self.repo.find_by_id(call_id)
        if not refreshed or user_id not in refreshed.get("participant_user_ids", []):
            return None
        return refreshed if refreshed.get("status") == "reconnecting" else None

    async def resume_call(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        ensure_status_in(
            current["status"],
            allowed_statuses=tuple(RECOVERABLE_CALL_STATUSES),
            message="Call is not awaiting reconnection",
        )

        updated = await self.repo.resume_reconnecting(
            call_id=call_id,
            participant_user_id=user_id,
        )
        if updated is not None:
            return updated
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def expire_call_if_due(self, *, call_id: str) -> CallTerminalResult | None:
        return await self._expire_call_if_due_with_history(call_id=call_id)

    async def build_session(
        self,
        *,
        call_doc: dict[str, Any] | CallDoc,
        viewer_user_id: str,
        include_ice_servers: bool = True,
    ) -> CallSession:
        model = self.to_call_doc(call_doc)
        peer_user_id = self._peer_user_id(call=model, viewer_user_id=viewer_user_id)
        peer = await self._build_peer_summary(peer_user_id)

        ice_servers: list[IceServer] = []
        if include_ice_servers and self.webrtc_service is not None:
            ice_servers = await self.webrtc_service.get_ice_servers()
        return CallSession(
            call=model,
            peer_user=peer,
            ice_servers=ice_servers,
        )

    def to_call_doc(self, doc: dict[str, Any] | CallDoc) -> CallDoc:
        if isinstance(doc, CallDoc):
            return doc

        return CallDoc(
            id=str(doc["_id"]),
            caller_user_id=doc["caller_user_id"],
            callee_user_id=doc["callee_user_id"],
            participant_user_ids=list(doc["participant_user_ids"]),
            type=doc["type"],
            status=doc["status"],
            room_id=doc["room_id"],
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            answered_at=doc.get("answered_at"),
            ended_at=doc.get("ended_at"),
            expires_at=doc.get("expires_at"),
            reconnect_deadline_at=doc.get("reconnect_deadline_at"),
            disconnected_user_ids=list(doc.get("disconnected_user_ids") or []),
            is_live=bool(doc.get("is_live", False)),
        )

    async def _get_participant_call(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self._expire_call_if_due_with_history(call_id=call_id)
        call = await self.repo.find_by_id(call_id)
        if not call or user_id not in call.get("participant_user_ids", []):
            raise AppError(code="CALL_NOT_FOUND", message="Call not found", status_code=404)
        return call

    async def _reload_after_conflict(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)
        self._raise_if_expired(current)
        raise AppError(
            code="INVALID_CALL_STATE",
            message=f"Call is already {current['status']}",
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
        return await self._build_terminal_result(expired)

    async def _build_terminal_result(self, call_doc: dict[str, Any]) -> CallTerminalResult:
        history_message = await self._ensure_history_message(call_doc=call_doc)
        return CallTerminalResult(
            call=self.to_call_doc(call_doc),
            history_message=history_message,
        )

    async def _ensure_history_message(self, *, call_doc: dict[str, Any]) -> MessageDoc | None:
        if call_doc.get("status") not in TERMINAL_CALL_STATUSES:
            return None
        if self.messages_repo is None:
            return None

        history_message_doc = await self.messages_repo.create_call_message(call_doc=call_doc)
        history_message_id = str(history_message_doc["_id"])
        if call_doc.get("history_message_id") != history_message_id:
            await self.repo.set_history_message_id(
                call_id=str(call_doc["_id"]),
                history_message_id=history_message_id,
            )
            call_doc["history_message_id"] = history_message_id

        return to_message_doc(history_message_doc)

    def _raise_if_expired(self, call_doc: dict[str, Any]) -> None:
        if call_doc["status"] == "expired":
            raise AppError(
                code="CALL_EXPIRED",
                message="Call expired",
                status_code=409,
            )

    def _peer_user_id(self, *, call: CallDoc, viewer_user_id: str) -> str:
        if viewer_user_id == call.caller_user_id:
            return call.callee_user_id
        return call.caller_user_id

    def _peer_user_id_from_doc(self, *, call_doc: dict[str, Any], viewer_user_id: str) -> str:
        if viewer_user_id == call_doc["caller_user_id"]:
            return call_doc["callee_user_id"]
        return call_doc["caller_user_id"]

    async def _build_peer_summary(self, peer_user_id: str) -> CallPeerUserSummary:
        peer = await self.users_repo.find_by_id(peer_user_id)
        is_online = (
            await self.presence_service.is_online(peer_user_id)
            if self.presence_service
            else False
        )
        return self._to_peer_summary(
            peer_user_id=peer_user_id,
            peer=peer,
            is_online=is_online,
        )

    def _to_peer_summary(
        self,
        *,
        peer_user_id: str,
        peer: dict[str, Any] | None,
        is_online: bool,
    ) -> CallPeerUserSummary:
        return CallPeerUserSummary(
            id=peer_user_id,
            username=peer.get("username", "") if peer else "",
            display_name=peer.get("display_name") if peer else None,
            avatar=build_user_avatar_payload(peer.get("avatar")) if peer else None,
            is_online=is_online,
        )

    async def _get_presence_map(self, *, user_ids: list[str]) -> dict[str, bool]:
        if not user_ids or self.presence_service is None:
            return {}
        statuses = await asyncio.gather(
            *(self.presence_service.is_online(user_id) for user_id in user_ids)
        )
        return dict(zip(user_ids, statuses))

    def _to_history_item(
        self,
        *,
        call_doc: dict[str, Any],
        viewer_user_id: str,
        users_by_id: dict[str, dict[str, Any]],
        online_by_id: dict[str, bool],
    ) -> CallHistoryItem:
        model = self.to_call_doc(call_doc)
        peer_user_id = self._peer_user_id(call=model, viewer_user_id=viewer_user_id)
        direction: CallDirection = (
            "outgoing" if viewer_user_id == model.caller_user_id else "incoming"
        )
        peer = self._to_peer_summary(
            peer_user_id=peer_user_id,
            peer=users_by_id.get(peer_user_id),
            is_online=online_by_id.get(peer_user_id, False),
        )
        return CallHistoryItem(
            id=model.id,
            peer_user=peer,
            direction=direction,
            type=model.type,
            status=model.status,
            started_at=model.created_at,
            answered_at=model.answered_at,
            ended_at=model.ended_at,
            duration_ms=self._duration_ms(model),
            message_id=call_doc.get("history_message_id"),
        )

    def _duration_ms(self, call: CallDoc) -> int:
        if call.answered_at is None or call.ended_at is None:
            return 0
        return max(int((call.ended_at - call.answered_at).total_seconds() * 1000), 0)

    def is_live_status(self, status: CallStatus) -> bool:
        return status in LIVE_CALL_STATUSES
