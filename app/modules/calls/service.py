from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import AppError
from app.modules.calls.repository import CallsRepository
from app.modules.calls.schemas import (
    CallDoc,
    CallPeerUserSummary,
    CallSession,
    CallStatus,
    CallType,
    IceServer,
)
from app.modules.calls.state import (
    LIVE_CALL_STATUSES,
    RECOVERABLE_CALL_STATUSES,
    ensure_status_in,
)
from app.modules.users.avatar import build_user_avatar_payload


class PingsServiceProto(Protocol):
    async def ensure_can_message(self, *, sender_id: str, receiver_id: str) -> None: ...


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> dict[str, Any] | None: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


class CallsService:
    def __init__(
        self,
        *,
        repo: CallsRepository,
        users_repo: UsersRepositoryProto,
        pings_service: PingsServiceProto,
        presence_service: PresenceServiceProto | None = None,
    ) -> None:
        self.repo = repo
        self.users_repo = users_repo
        self.pings_service = pings_service
        self.presence_service = presence_service

    async def expire_stale_calls(self) -> int:
        return await self.repo.expire_stale_calls()

    async def get_active_call(self, *, user_id: str) -> dict[str, Any] | None:
        await self.repo.expire_stale_calls()
        return await self.repo.find_live_call_for_user(
            user_id=user_id,
            statuses=RECOVERABLE_CALL_STATUSES,
        )

    async def get_participant_call(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.repo.expire_stale_calls()
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

    async def create_call(
        self,
        *,
        caller_user_id: str,
        callee_user_id: str,
        call_type: CallType,
    ) -> dict[str, Any]:
        await self.repo.expire_stale_calls()

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
        await self.repo.expire_stale_calls()
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

    async def reject_call(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.repo.expire_stale_calls()
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
            return updated
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def end_call(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.repo.expire_stale_calls()
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
            return updated
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def start_connecting(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.repo.expire_stale_calls()
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
        await self.repo.expire_stale_calls()
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
        await self.repo.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        ensure_status_in(current["status"], allowed_statuses=("connecting", "active"))
        return current

    async def mark_active(self, *, user_id: str, call_id: str) -> dict[str, Any]:
        await self.repo.expire_stale_calls()
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
        await self.repo.expire_stale_calls()

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
        await self.repo.expire_stale_calls()
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

    async def expire_call_if_due(self, *, call_id: str) -> dict[str, Any] | None:
        return await self.repo.expire_call_if_due(call_id=call_id)

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

        ice_servers = self._build_ice_servers(user_id=viewer_user_id) if include_ice_servers else []
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
        await self.repo.expire_call_if_due(call_id=call_id)
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

    async def _build_peer_summary(self, peer_user_id: str) -> CallPeerUserSummary:
        peer = await self.users_repo.find_by_id(peer_user_id)
        is_online = await self.presence_service.is_online(peer_user_id) if self.presence_service else False

        return CallPeerUserSummary(
            id=peer_user_id,
            username=peer.get("username", "") if peer else "",
            display_name=peer.get("display_name") if peer else None,
            avatar=build_user_avatar_payload(peer.get("avatar")) if peer else None,
            is_online=is_online,
        )

    def _build_ice_servers(self, *, user_id: str) -> list[IceServer]:
        servers: list[IceServer] = []

        if settings.call_stun_urls:
            stun_urls: str | list[str]
            stun_urls = settings.call_stun_urls[0] if len(settings.call_stun_urls) == 1 else settings.call_stun_urls
            servers.append(IceServer(urls=stun_urls))

        if settings.call_turn_urls and settings.turn_auth_secret:
            username = self._build_turn_username(user_id=user_id)
            credential = self._build_turn_credential(username=username)
            turn_urls: str | list[str]
            turn_urls = settings.call_turn_urls[0] if len(settings.call_turn_urls) == 1 else settings.call_turn_urls
            servers.append(
                IceServer(
                    urls=turn_urls,
                    username=username,
                    credential=credential,
                )
            )

        return servers

    def _build_turn_username(self, *, user_id: str) -> str:
        expires_at = int(
            (datetime.now(UTC) + timedelta(seconds=settings.turn_credential_ttl_seconds)).timestamp()
        )
        return f"{expires_at}:{user_id}"

    def _build_turn_credential(self, *, username: str) -> str:
        digest = hmac.new(
            settings.turn_auth_secret.encode("utf-8"),
            username.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def is_live_status(self, status: CallStatus) -> bool:
        return status in LIVE_CALL_STATUSES
