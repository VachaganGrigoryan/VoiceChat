from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.errors import AppError
from app.db.models import CallDocument
from app.modules.calls.schemas import CallType
from app.modules.calls.service.base import CallTerminalResult
from app.modules.calls.state import (
    LIVE_CALL_STATUSES,
    RECOVERABLE_CALL_STATUSES,
    ensure_status_in,
)


class LifecycleCallsMixin:
    async def create_call(
        self,
        *,
        caller_user_id: str,
        callee_user_id: str,
        call_type: CallType,
    ) -> CallDocument:
        await self.expire_stale_calls()

        if caller_user_id == callee_user_id:
            raise AppError(
                code="INVALID_CALL_TARGET",
                message="Cannot call yourself",
                status_code=400,
            )

        target = await self.users_repo.find_by_id(callee_user_id)
        if not target:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )

        await self.connection_service.ensure_can_message(
            sender_id=caller_user_id,
            receiver_id=callee_user_id,
        )
        if self.conversations_service is None:
            raise AppError(
                code="CALL_CONVERSATION_UNAVAILABLE",
                message="Call conversation is unavailable",
                status_code=503,
            )
        conversation = await self.conversations_service.ensure_dm_conversation(
            user_id=caller_user_id,
            peer_user_id=callee_user_id,
        )

        expires_at = datetime.now(UTC) + timedelta(
            seconds=settings.call_ring_timeout_seconds
        )
        return self._as_call_document(
            await self.repo.create_call(
                conversation_id=conversation.str_id,
                caller_user_id=caller_user_id,
                callee_user_id=callee_user_id,
                call_type=call_type,
                expires_at=expires_at,
            )
        )

    async def accept_call(self, *, user_id: str, call_id: str) -> CallDocument:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        if user_id != current.callee_user_id:
            raise AppError(
                code="CALL_FORBIDDEN",
                message="Only the callee can accept this call",
                status_code=403,
            )

        self._raise_if_expired(current)
        ensure_status_in(current.status, allowed_statuses=("ringing",))

        updated = await self.repo.accept_call(call_id=call_id, callee_user_id=user_id)
        if updated is not None:
            joined = await self.repo.update_participant_state(
                call_id=call_id,
                participant_user_id=user_id,
                join_state="joined",
            )
            return self._as_call_document(joined or updated)
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def reject_call(self, *, user_id: str, call_id: str) -> CallTerminalResult:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        if user_id != current.callee_user_id:
            raise AppError(
                code="CALL_FORBIDDEN",
                message="Only the callee can reject this call",
                status_code=403,
            )

        self._raise_if_expired(current)
        ensure_status_in(current.status, allowed_statuses=("ringing",))

        updated = await self.repo.reject_call(call_id=call_id, callee_user_id=user_id)
        if updated is not None:
            return await self._build_terminal_result(self._as_call_document(updated))
        await self._reload_after_conflict(user_id=user_id, call_id=call_id)
        raise AssertionError("unreachable")

    async def end_call(self, *, user_id: str, call_id: str) -> CallTerminalResult:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        status = current.status

        if status == "ringing":
            if user_id == current.caller_user_id:
                updated = await self.repo.cancel_call(
                    call_id=call_id, caller_user_id=user_id
                )
            elif user_id == current.callee_user_id:
                updated = await self.repo.reject_call(
                    call_id=call_id, callee_user_id=user_id
                )
            else:
                updated = None
        elif status in {"accepted", "connecting", "active", "reconnecting"}:
            updated = await self.repo.end_call(
                call_id=call_id, participant_user_id=user_id
            )
        else:
            updated = None

        if updated is not None:
            return await self._build_terminal_result(self._as_call_document(updated))
        await self._reload_after_conflict(user_id=user_id, call_id=call_id)
        raise AssertionError("unreachable")

    async def start_connecting(self, *, user_id: str, call_id: str) -> CallDocument:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        if user_id != current.caller_user_id:
            raise AppError(
                code="CALL_FORBIDDEN",
                message="Only the caller can send an offer",
                status_code=403,
            )

        self._raise_if_expired(current)
        ensure_status_in(
            current.status,
            allowed_statuses=("accepted", "active", "connecting", "reconnecting"),
        )

        if current.status == "connecting":
            return current

        updated = await self.repo.set_connecting(
            call_id=call_id, caller_user_id=user_id
        )
        if updated is not None:
            return self._as_call_document(updated)
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def ensure_answer_allowed(
        self, *, user_id: str, call_id: str
    ) -> CallDocument:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        if user_id != current.callee_user_id:
            raise AppError(
                code="CALL_FORBIDDEN",
                message="Only the callee can send an answer",
                status_code=403,
            )

        self._raise_if_expired(current)
        ensure_status_in(current.status, allowed_statuses=("connecting",))
        return current

    async def ensure_ice_candidate_allowed(
        self, *, user_id: str, call_id: str
    ) -> CallDocument:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        ensure_status_in(current.status, allowed_statuses=("connecting", "active"))
        return current

    async def mark_active(self, *, user_id: str, call_id: str) -> CallDocument:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        if current.status == "active":
            return current

        ensure_status_in(current.status, allowed_statuses=("connecting",))

        updated = await self.repo.set_active(
            call_id=call_id, participant_user_id=user_id
        )
        if updated is not None:
            return self._as_call_document(updated)
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def mark_reconnecting_from_disconnect(
        self,
        *,
        user_id: str,
        call_id: str,
    ) -> CallDocument | None:
        await self.expire_stale_calls()

        current = await self.repo.find_by_id(call_id)
        if current is not None:
            current = self._as_call_document(current)
        if not current or user_id not in current.participant_user_ids:
            return None

        self._raise_if_expired(current)
        if current.status not in RECOVERABLE_CALL_STATUSES:
            return None

        deadline = datetime.now(UTC) + timedelta(
            seconds=settings.call_reconnect_grace_seconds
        )
        updated = await self.repo.mark_reconnecting(
            call_id=call_id,
            participant_user_id=user_id,
            reconnect_deadline_at=deadline,
        )
        if updated is not None:
            return self._as_call_document(updated)

        refreshed = await self.repo.find_by_id(call_id)
        if refreshed is not None:
            refreshed = self._as_call_document(refreshed)
        if not refreshed or user_id not in refreshed.participant_user_ids:
            return None
        return refreshed if refreshed.status == "reconnecting" else None

    async def resume_call(self, *, user_id: str, call_id: str) -> CallDocument:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        ensure_status_in(
            current.status,
            allowed_statuses=tuple(RECOVERABLE_CALL_STATUSES),
            message="Call is not awaiting reconnection",
        )

        updated = await self.repo.resume_reconnecting(
            call_id=call_id,
            participant_user_id=user_id,
        )
        if updated is not None:
            updated = self._as_call_document(updated)
            if updated.status == "reconnecting" and not updated.disconnected_user_ids:
                connecting = await self.repo.set_connecting_after_resume(
                    call_id=call_id,
                    participant_user_id=user_id,
                )
                if connecting is not None:
                    return self._as_call_document(connecting)

                refreshed = await self.repo.find_by_id(call_id)
                if refreshed is not None:
                    refreshed = self._as_call_document(refreshed)
                if refreshed is not None and user_id in refreshed.participant_user_ids:
                    return refreshed
            return updated
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def mark_participant_joined(
        self, *, user_id: str, call_id: str
    ) -> CallDocument:
        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        ensure_status_in(
            current.status,
            allowed_statuses=tuple(LIVE_CALL_STATUSES),
            message="Call is not live",
        )

        participant_state = self.to_call_doc(current).participant_states[user_id]
        if participant_state.join_state == "joined":
            return current

        updated = await self.repo.update_participant_state(
            call_id=call_id,
            participant_user_id=user_id,
            join_state="joined",
        )
        if updated is not None:
            return self._as_call_document(updated)
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)

    async def update_media_state(
        self,
        *,
        user_id: str,
        call_id: str,
        audio_enabled: bool | None = None,
        video_enabled: bool | None = None,
    ) -> CallDocument:
        if audio_enabled is None and video_enabled is None:
            raise AppError(
                code="INVALID_CALL_MEDIA_STATE",
                message="At least one media state field is required",
                status_code=400,
            )

        await self.expire_stale_calls()
        current = await self._get_participant_call(user_id=user_id, call_id=call_id)

        self._raise_if_expired(current)
        ensure_status_in(
            current.status,
            allowed_statuses=tuple(LIVE_CALL_STATUSES),
            message="Call is not live",
        )

        if current.type == "audio" and video_enabled is True:
            raise AppError(
                code="INVALID_CALL_MEDIA_STATE",
                message="Video cannot be enabled for an audio call",
                status_code=409,
            )

        participant_state = self.to_call_doc(current).participant_states[user_id]
        if (
            audio_enabled is None or audio_enabled == participant_state.audio_enabled
        ) and (
            video_enabled is None or video_enabled == participant_state.video_enabled
        ):
            return current

        update_kwargs: dict[str, Any] = {}
        if audio_enabled is not None:
            update_kwargs["audio_enabled"] = audio_enabled
        if video_enabled is not None:
            update_kwargs["video_enabled"] = video_enabled

        updated = await self.repo.update_participant_state(
            call_id=call_id,
            participant_user_id=user_id,
            **update_kwargs,
        )
        if updated is not None:
            return self._as_call_document(updated)
        return await self._reload_after_conflict(user_id=user_id, call_id=call_id)
