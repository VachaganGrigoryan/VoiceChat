from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from app.db.models import CallDocument, UserDocument
from app.modules.calls.schemas import (
    CallDirection,
    CallDoc,
    CallHistoryItem,
    CallParticipantJoinState,
    CallParticipantState,
    CallPeerUserSummary,
    CallStatus,
)
from app.modules.users.avatar import build_user_avatar_payload


class MappingCallsMixin:
    def _user_value(
        self,
        user: UserDocument | dict[str, Any],
        key: str,
        default: Any = None,
    ) -> Any:
        if isinstance(user, dict):
            value = user.get("_id", default) if key == "id" else user.get(key, default)
        else:
            value = getattr(user, key, default)
        return str(value) if isinstance(value, ObjectId) else value

    def to_call_doc(self, doc: dict[str, Any] | CallDocument | CallDoc) -> CallDoc:
        if isinstance(doc, CallDoc):
            return doc
        if isinstance(doc, CallDocument):
            participant_states = self._normalize_participant_states(doc)
            return CallDoc(
                id=str(doc.id) if doc.id else "",
                caller_user_id=doc.caller_user_id,
                callee_user_id=doc.callee_user_id,
                participant_user_ids=list(doc.participant_user_ids),
                type=doc.type,
                status=doc.status,
                room_id=doc.room_id,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
                answered_at=doc.answered_at,
                ended_at=doc.ended_at,
                expires_at=doc.expires_at,
                reconnect_deadline_at=doc.reconnect_deadline_at,
                disconnected_user_ids=list(doc.disconnected_user_ids),
                participant_states=participant_states,
                is_live=doc.is_live,
            )

        participant_states = self._normalize_participant_states(doc)

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
            participant_states=participant_states,
            is_live=bool(doc.get("is_live", False)),
        )

    def _normalize_participant_states(
        self,
        call_doc: dict[str, Any] | CallDocument,
    ) -> dict[str, CallParticipantState]:
        defaults = self._default_participant_states(call_doc)
        raw_states = (
            call_doc.participant_states
            if isinstance(call_doc, CallDocument)
            else call_doc.get("participant_states") or {}
        )

        for user_id, raw_state in raw_states.items():
            if user_id not in defaults:
                continue

            payload = (
                raw_state.model_dump()
                if isinstance(raw_state, CallParticipantState)
                else dict(raw_state)
            )
            payload.setdefault("updated_at", defaults[user_id].updated_at)
            payload.setdefault("role", defaults[user_id].role)
            payload.setdefault("join_state", defaults[user_id].join_state)
            payload.setdefault("audio_enabled", defaults[user_id].audio_enabled)
            payload.setdefault("video_enabled", defaults[user_id].video_enabled)
            defaults[user_id] = CallParticipantState.model_validate(payload)

        return defaults

    def _default_participant_states(
        self,
        call_doc: dict[str, Any] | CallDocument,
    ) -> dict[str, CallParticipantState]:
        if isinstance(call_doc, CallDocument):
            timestamp = call_doc.updated_at or call_doc.created_at or datetime.now(UTC)
            call_type = call_doc.type
            status = call_doc.status
            disconnected_user_ids = set(call_doc.disconnected_user_ids)
            caller_user_id = call_doc.caller_user_id
            callee_user_id = call_doc.callee_user_id
        else:
            timestamp = (
                call_doc.get("updated_at")
                or call_doc.get("created_at")
                or datetime.now(UTC)
            )
            call_type = call_doc["type"]
            status = call_doc["status"]
            disconnected_user_ids = set(call_doc.get("disconnected_user_ids") or [])
            caller_user_id = call_doc["caller_user_id"]
            callee_user_id = call_doc["callee_user_id"]

        return {
            caller_user_id: CallParticipantState(
                role="caller",
                join_state=self._infer_join_state(
                    status=status,
                    role="caller",
                    user_id=caller_user_id,
                    disconnected_user_ids=disconnected_user_ids,
                ),
                audio_enabled=True,
                video_enabled=call_type == "video",
                updated_at=timestamp,
            ),
            callee_user_id: CallParticipantState(
                role="callee",
                join_state=self._infer_join_state(
                    status=status,
                    role="callee",
                    user_id=callee_user_id,
                    disconnected_user_ids=disconnected_user_ids,
                ),
                audio_enabled=True,
                video_enabled=call_type == "video",
                updated_at=timestamp,
            ),
        }

    def _infer_join_state(
        self,
        *,
        status: CallStatus,
        role: str,
        user_id: str,
        disconnected_user_ids: set[str],
    ) -> CallParticipantJoinState:
        if user_id in disconnected_user_ids:
            return "disconnected"
        if status == "ringing":
            return "waiting"
        if status == "accepted":
            return "joined" if role == "callee" else "waiting"
        if status in {"connecting", "active", "reconnecting"}:
            return "joined"
        return "waiting"

    def _peer_user_id(self, *, call: CallDoc, viewer_user_id: str) -> str:
        if viewer_user_id == call.caller_user_id:
            return call.callee_user_id
        return call.caller_user_id

    def _peer_user_id_from_doc(
        self, *, call_doc: CallDocument, viewer_user_id: str
    ) -> str:
        if viewer_user_id == call_doc.caller_user_id:
            return call_doc.callee_user_id
        return call_doc.caller_user_id

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
        peer: UserDocument | None,
        is_online: bool,
    ) -> CallPeerUserSummary:
        return CallPeerUserSummary(
            id=peer_user_id,
            username=self._user_value(peer, "username", "") if peer else "",
            display_name=self._user_value(peer, "display_name") if peer else None,
            avatar=(
                build_user_avatar_payload(self._user_value(peer, "avatar"))
                if peer
                else None
            ),
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
        call_doc: CallDocument,
        viewer_user_id: str,
        users_by_id: dict[str, UserDocument],
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
            message_id=call_doc.history_message_id,
        )

    def _duration_ms(self, call: CallDoc) -> int:
        if call.answered_at is None or call.ended_at is None:
            return 0
        return max(int((call.ended_at - call.answered_at).total_seconds() * 1000), 0)
