from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.db.indexes import COL_CALLS
from app.modules.calls.schemas import CallType

_MISSING = object()


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as exc:
        raise AppError(code="INVALID_ID", message="Invalid id", status_code=400) from exc


class CallsRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db[COL_CALLS]

    async def create_call(
        self,
        *,
        caller_user_id: str,
        callee_user_id: str,
        call_type: CallType,
        expires_at: datetime,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        call_id = ObjectId()

        doc = {
            "_id": call_id,
            "caller_user_id": caller_user_id,
            "callee_user_id": callee_user_id,
            "participant_user_ids": [caller_user_id, callee_user_id],
            "type": call_type,
            "status": "ringing",
            "room_id": f"call:{call_id}",
            "created_at": now,
            "updated_at": now,
            "answered_at": None,
            "ended_at": None,
            "expires_at": expires_at,
            "reconnect_deadline_at": None,
            "disconnected_user_ids": [],
            "is_live": True,
        }

        try:
            await self.col.insert_one(doc)
        except DuplicateKeyError as exc:
            raise AppError(
                code="CALL_BUSY",
                message="A participant is already in another live call",
                status_code=409,
            ) from exc

        return doc

    async def find_by_id(self, call_id: str) -> dict[str, Any] | None:
        return await self.col.find_one({"_id": _oid(call_id)})

    async def find_live_call_for_user(
        self,
        *,
        user_id: str,
        statuses: Iterable[str],
    ) -> dict[str, Any] | None:
        return await self.col.find_one(
            {
                "participant_user_ids": user_id,
                "status": {"$in": list(statuses)},
                "is_live": True,
            },
            sort=[("updated_at", -1), ("_id", -1)],
        )

    async def find_live_calls(
        self,
        *,
        statuses: Iterable[str],
    ) -> list[dict[str, Any]]:
        cursor = self.col.find(
            {
                "status": {"$in": list(statuses)},
                "is_live": True,
            }
        )
        return await cursor.to_list(length=None)

    async def expire_stale_calls(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        expired_result = await self.col.update_many(
            {
                "status": "ringing",
                "is_live": True,
                "expires_at": {"$lte": current_time},
            },
            {
                "$set": {
                    "status": "expired",
                    "is_live": False,
                    "ended_at": current_time,
                    "updated_at": current_time,
                    "reconnect_deadline_at": None,
                    "disconnected_user_ids": [],
                }
            },
        )
        reconnect_result = await self.col.update_many(
            {
                "status": "reconnecting",
                "is_live": True,
                "reconnect_deadline_at": {"$lte": current_time},
            },
            {
                "$set": {
                    "status": "ended",
                    "is_live": False,
                    "ended_at": current_time,
                    "updated_at": current_time,
                    "reconnect_deadline_at": None,
                    "disconnected_user_ids": [],
                }
            },
        )
        return int(expired_result.modified_count + reconnect_result.modified_count)

    async def expire_call_if_due(
        self,
        *,
        call_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        current_time = now or datetime.now(UTC)
        expired = await self.col.find_one_and_update(
            {
                "_id": _oid(call_id),
                "status": "ringing",
                "is_live": True,
                "expires_at": {"$lte": current_time},
            },
            {
                "$set": {
                    "status": "expired",
                    "is_live": False,
                    "ended_at": current_time,
                    "updated_at": current_time,
                    "reconnect_deadline_at": None,
                    "disconnected_user_ids": [],
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if expired is not None:
            return expired

        return await self.col.find_one_and_update(
            {
                "_id": _oid(call_id),
                "status": "reconnecting",
                "is_live": True,
                "reconnect_deadline_at": {"$lte": current_time},
            },
            {
                "$set": {
                    "status": "ended",
                    "is_live": False,
                    "ended_at": current_time,
                    "updated_at": current_time,
                    "reconnect_deadline_at": None,
                    "disconnected_user_ids": [],
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def accept_call(self, *, call_id: str, callee_user_id: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        return await self._transition_status(
            call_id=call_id,
            expected_statuses=("ringing",),
            next_status="accepted",
            is_live=True,
            extra_filter={
                "callee_user_id": callee_user_id,
                "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
            },
            answered_at=now,
            expires_at=None,
            reconnect_deadline_at=None,
            disconnected_user_ids=[],
        )

    async def reject_call(self, *, call_id: str, callee_user_id: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        return await self._transition_status(
            call_id=call_id,
            expected_statuses=("ringing",),
            next_status="rejected",
            is_live=False,
            extra_filter={
                "callee_user_id": callee_user_id,
                "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
            },
            ended_at=now,
            expires_at=None,
            reconnect_deadline_at=None,
            disconnected_user_ids=[],
        )

    async def cancel_call(self, *, call_id: str, caller_user_id: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        return await self._transition_status(
            call_id=call_id,
            expected_statuses=("ringing",),
            next_status="cancelled",
            is_live=False,
            extra_filter={
                "caller_user_id": caller_user_id,
                "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
            },
            ended_at=now,
            expires_at=None,
            reconnect_deadline_at=None,
            disconnected_user_ids=[],
        )

    async def end_call(self, *, call_id: str, participant_user_id: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        return await self._transition_status(
            call_id=call_id,
            expected_statuses=("accepted", "connecting", "active", "reconnecting"),
            next_status="ended",
            is_live=False,
            extra_filter={"participant_user_ids": participant_user_id},
            ended_at=now,
            expires_at=None,
            reconnect_deadline_at=None,
            disconnected_user_ids=[],
        )

    async def set_connecting(self, *, call_id: str, caller_user_id: str) -> dict[str, Any] | None:
        return await self._transition_status(
            call_id=call_id,
            expected_statuses=("accepted", "reconnecting"),
            next_status="connecting",
            is_live=True,
            extra_filter={"caller_user_id": caller_user_id},
            reconnect_deadline_at=None,
            disconnected_user_ids=[],
        )

    async def set_active(self, *, call_id: str, participant_user_id: str) -> dict[str, Any] | None:
        return await self._transition_status(
            call_id=call_id,
            expected_statuses=("connecting",),
            next_status="active",
            is_live=True,
            extra_filter={"participant_user_ids": participant_user_id},
            reconnect_deadline_at=None,
            disconnected_user_ids=[],
        )

    async def mark_reconnecting(
        self,
        *,
        call_id: str,
        participant_user_id: str,
        reconnect_deadline_at: datetime,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        return await self.col.find_one_and_update(
            {
                "_id": _oid(call_id),
                "status": {"$in": ["accepted", "connecting", "active", "reconnecting"]},
                "participant_user_ids": participant_user_id,
                "is_live": True,
            },
            {
                "$set": {
                    "status": "reconnecting",
                    "updated_at": now,
                    "reconnect_deadline_at": reconnect_deadline_at,
                },
                "$addToSet": {
                    "disconnected_user_ids": participant_user_id,
                },
            },
            return_document=ReturnDocument.AFTER,
        )

    async def resume_reconnecting(
        self,
        *,
        call_id: str,
        participant_user_id: str,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        return await self.col.find_one_and_update(
            {
                "_id": _oid(call_id),
                "status": {"$in": ["accepted", "connecting", "active", "reconnecting"]},
                "participant_user_ids": participant_user_id,
                "is_live": True,
            },
            {
                "$set": {
                    "updated_at": now,
                },
                "$pull": {
                    "disconnected_user_ids": participant_user_id,
                },
            },
            return_document=ReturnDocument.AFTER,
        )

    async def _transition_status(
        self,
        *,
        call_id: str,
        expected_statuses: Iterable[str],
        next_status: str,
        is_live: bool,
        extra_filter: dict[str, Any] | None = None,
        answered_at: datetime | object = _MISSING,
        ended_at: datetime | object = _MISSING,
        expires_at: datetime | None | object = _MISSING,
        reconnect_deadline_at: datetime | None | object = _MISSING,
        disconnected_user_ids: list[str] | object = _MISSING,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        update_fields: dict[str, Any] = {
            "status": next_status,
            "is_live": is_live,
            "updated_at": now,
        }

        if answered_at is not _MISSING:
            update_fields["answered_at"] = answered_at
        if ended_at is not _MISSING:
            update_fields["ended_at"] = ended_at
        if expires_at is not _MISSING:
            update_fields["expires_at"] = expires_at
        if reconnect_deadline_at is not _MISSING:
            update_fields["reconnect_deadline_at"] = reconnect_deadline_at
        if disconnected_user_ids is not _MISSING:
            update_fields["disconnected_user_ids"] = disconnected_user_ids

        filter_doc: dict[str, Any] = {
            "_id": _oid(call_id),
            "status": {"$in": list(expected_statuses)},
        }
        if extra_filter:
            filter_doc.update(extra_filter)

        return await self.col.find_one_and_update(
            filter_doc,
            {"$set": update_fields},
            return_document=ReturnDocument.AFTER,
        )
