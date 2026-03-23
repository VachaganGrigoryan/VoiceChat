from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.db.indexes import (
    COL_MESSAGES,
    COL_STICKER_PACKS,
    COL_STICKERS,
    COL_STICKER_UPLOAD_SESSIONS,
)


def _oid(value: str | ObjectId) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except Exception as exc:
        raise AppError(code="INVALID_ID", message="Invalid id", status_code=400) from exc


class StickersRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.packs = db[COL_STICKER_PACKS]
        self.stickers = db[COL_STICKERS]
        self.upload_sessions = db[COL_STICKER_UPLOAD_SESSIONS]
        self.messages = db[COL_MESSAGES]

    async def create_pack(
        self,
        *,
        owner_user_id: str,
        slug: str,
        title: str,
        description: str | None,
        visibility: str,
        tags: list[str],
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        doc = {
            "owner_user_id": _oid(owner_user_id),
            "slug": slug,
            "title": title,
            "description": description,
            "cover_sticker_id": None,
            "visibility": visibility,
            "status": "draft",
            "kind": "custom",
            "tags": tags,
            "sticker_count": 0,
            "is_deleted": False,
            "created_at": now,
            "updated_at": now,
            "published_at": None,
        }

        try:
            result = await self.packs.insert_one(doc)
        except DuplicateKeyError as exc:
            raise AppError(
                code="STICKER_PACK_SLUG_TAKEN",
                message="Sticker pack slug is already taken",
                status_code=409,
            ) from exc

        doc["_id"] = result.inserted_id
        return doc

    async def list_owner_packs(self, *, owner_user_id: str) -> list[dict[str, Any]]:
        cursor = self.packs.find(
            {
                "owner_user_id": _oid(owner_user_id),
                "is_deleted": False,
            }
        ).sort([("updated_at", -1), ("_id", -1)])
        return await cursor.to_list(length=None)

    async def get_pack_for_owner(
        self,
        *,
        pack_id: str,
        owner_user_id: str,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {
            "_id": _oid(pack_id),
            "owner_user_id": _oid(owner_user_id),
        }
        if not include_deleted:
            query["is_deleted"] = False
        return await self.packs.find_one(query)

    async def update_pack(
        self,
        *,
        pack_id: str,
        owner_user_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        if not updates:
            existing = await self.get_pack_for_owner(pack_id=pack_id, owner_user_id=owner_user_id)
            if not existing:
                raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)
            return existing

        updates = {
            **updates,
            "updated_at": datetime.now(UTC),
        }

        try:
            updated = await self.packs.find_one_and_update(
                {
                    "_id": _oid(pack_id),
                    "owner_user_id": _oid(owner_user_id),
                    "is_deleted": False,
                },
                {"$set": updates},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise AppError(
                code="STICKER_PACK_SLUG_TAKEN",
                message="Sticker pack slug is already taken",
                status_code=409,
            ) from exc

        if updated is None:
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)
        return updated

    async def publish_pack(self, *, pack_id: str, owner_user_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        updated = await self.packs.find_one_and_update(
            {
                "_id": _oid(pack_id),
                "owner_user_id": _oid(owner_user_id),
                "is_deleted": False,
                "status": "draft",
            },
            {
                "$set": {
                    "status": "active",
                    "published_at": now,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)
        return updated

    async def soft_delete_pack(self, *, pack_id: str, owner_user_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        updated = await self.packs.find_one_and_update(
            {
                "_id": _oid(pack_id),
                "owner_user_id": _oid(owner_user_id),
                "is_deleted": False,
            },
            {
                "$set": {
                    "is_deleted": True,
                    "status": "archived",
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)
        return updated

    async def create_upload_session(
        self,
        *,
        session_id: ObjectId,
        owner_user_id: str,
        pack_id: str,
        storage: str,
        filename: str,
        content_type: str,
        expected_size: int,
        storage_key: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        doc = {
            "_id": session_id,
            "owner_user_id": _oid(owner_user_id),
            "pack_id": _oid(pack_id),
            "storage": storage,
            "status": "pending",
            "filename": filename,
            "content_type": content_type,
            "expected_size": expected_size,
            "storage_key": storage_key,
            "expires_at": expires_at,
            "created_at": datetime.now(UTC),
            "completed_at": None,
        }
        await self.upload_sessions.insert_one(doc)
        return doc

    async def get_upload_session(
        self,
        *,
        upload_session_id: str,
        owner_user_id: str | None = None,
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {"_id": _oid(upload_session_id)}
        if owner_user_id is not None:
            query["owner_user_id"] = _oid(owner_user_id)
        return await self.upload_sessions.find_one(query)

    async def update_upload_session_status(
        self,
        *,
        upload_session_id: str,
        owner_user_id: str,
        status: str,
        completed: bool = False,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        updates: dict[str, Any] = {"status": status}
        if completed:
            updates["completed_at"] = now

        updated = await self.upload_sessions.find_one_and_update(
            {
                "_id": _oid(upload_session_id),
                "owner_user_id": _oid(owner_user_id),
            },
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise AppError(code="STICKER_UPLOAD_SESSION_NOT_FOUND", message="Upload session not found", status_code=404)
        return updated

    async def next_sticker_sort_order(self, *, pack_id: str) -> int:
        existing = await self.stickers.find_one(
            {
                "pack_id": _oid(pack_id),
                "status": {"$ne": "archived"},
            },
            sort=[("sort_order", -1), ("created_at", -1), ("_id", -1)],
        )
        if not existing:
            return 1
        return int(existing.get("sort_order", 0)) + 1

    async def create_sticker(
        self,
        *,
        sticker_id: ObjectId,
        pack_id: str,
        created_by_user_id: str,
        storage: str,
        slug: str,
        title: str,
        emoji_aliases: list[str],
        storage_key: str,
        thumbnail_storage_key: str,
        width: int,
        height: int,
        file_size: int,
        checksum_sha256: str,
        sort_order: int,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        doc = {
            "_id": sticker_id,
            "pack_id": _oid(pack_id),
            "storage": storage,
            "slug": slug,
            "title": title,
            "emoji_aliases": emoji_aliases,
            "file_kind": "webp",
            "mime_type": "image/webp",
            "storage_key": storage_key,
            "thumbnail_storage_key": thumbnail_storage_key,
            "width": width,
            "height": height,
            "file_size": file_size,
            "checksum_sha256": checksum_sha256,
            "status": "active",
            "sort_order": sort_order,
            "version": 1,
            "is_animated": False,
            "created_by_user_id": _oid(created_by_user_id),
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self.stickers.insert_one(doc)
        except DuplicateKeyError as exc:
            raise AppError(
                code="STICKER_SLUG_TAKEN",
                message="Sticker slug is already taken in this pack",
                status_code=409,
            ) from exc
        return doc

    async def list_pack_stickers(self, *, pack_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"pack_id": _oid(pack_id)}
        if not include_archived:
            query["status"] = {"$ne": "archived"}
        cursor = self.stickers.find(query).sort([("sort_order", 1), ("created_at", 1), ("_id", 1)])
        return await cursor.to_list(length=None)

    async def get_joined_sticker_by_id(self, *, sticker_id: str) -> dict[str, Any] | None:
        pipeline = [
            {"$match": {"_id": _oid(sticker_id)}},
            {
                "$lookup": {
                    "from": COL_STICKER_PACKS,
                    "localField": "pack_id",
                    "foreignField": "_id",
                    "as": "pack",
                }
            },
            {"$unwind": "$pack"},
        ]
        rows = await self.stickers.aggregate(pipeline).to_list(length=1)
        return rows[0] if rows else None

    async def get_joined_sticker_for_owner(
        self,
        *,
        sticker_id: str,
        owner_user_id: str,
    ) -> dict[str, Any] | None:
        pipeline = [
            {"$match": {"_id": _oid(sticker_id)}},
            {
                "$lookup": {
                    "from": COL_STICKER_PACKS,
                    "localField": "pack_id",
                    "foreignField": "_id",
                    "as": "pack",
                }
            },
            {"$unwind": "$pack"},
            {
                "$match": {
                    "pack.owner_user_id": _oid(owner_user_id),
                }
            },
        ]
        rows = await self.stickers.aggregate(pipeline).to_list(length=1)
        return rows[0] if rows else None

    async def get_joined_sticker_by_ref_for_owner(
        self,
        *,
        pack_slug: str,
        sticker_slug: str,
        owner_user_id: str,
    ) -> dict[str, Any] | None:
        pipeline = [
            {"$match": {"slug": sticker_slug, "status": {"$ne": "archived"}}},
            {
                "$lookup": {
                    "from": COL_STICKER_PACKS,
                    "localField": "pack_id",
                    "foreignField": "_id",
                    "as": "pack",
                }
            },
            {"$unwind": "$pack"},
            {
                "$match": {
                    "pack.slug": pack_slug,
                    "pack.owner_user_id": _oid(owner_user_id),
                    "pack.is_deleted": False,
                }
            },
        ]
        rows = await self.stickers.aggregate(pipeline).to_list(length=1)
        return rows[0] if rows else None

    async def search_joined_stickers_by_emoji(
        self,
        *,
        owner_user_id: str,
        emoji: str,
    ) -> list[dict[str, Any]]:
        pipeline = [
            {
                "$match": {
                    "emoji_aliases": emoji,
                    "status": {"$ne": "archived"},
                }
            },
            {
                "$lookup": {
                    "from": COL_STICKER_PACKS,
                    "localField": "pack_id",
                    "foreignField": "_id",
                    "as": "pack",
                }
            },
            {"$unwind": "$pack"},
            {
                "$match": {
                    "pack.owner_user_id": _oid(owner_user_id),
                    "pack.is_deleted": False,
                }
            },
            {"$sort": {"sort_order": 1, "created_at": 1, "_id": 1}},
        ]
        return await self.stickers.aggregate(pipeline).to_list(length=None)

    async def update_sticker(
        self,
        *,
        sticker_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        if not updates:
            existing = await self.get_joined_sticker_by_id(sticker_id=sticker_id)
            if not existing:
                raise AppError(code="STICKER_NOT_FOUND", message="Sticker not found", status_code=404)
            return existing

        updates = {
            **updates,
            "updated_at": datetime.now(UTC),
        }

        try:
            updated = await self.stickers.find_one_and_update(
                {"_id": _oid(sticker_id)},
                {"$set": updates},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise AppError(
                code="STICKER_SLUG_TAKEN",
                message="Sticker slug is already taken in this pack",
                status_code=409,
            ) from exc

        if updated is None:
            raise AppError(code="STICKER_NOT_FOUND", message="Sticker not found", status_code=404)
        return updated

    async def refresh_pack_stats(self, *, pack_id: str) -> dict[str, Any]:
        pack_oid = _oid(pack_id)
        sticker_count = await self.stickers.count_documents(
            {
                "pack_id": pack_oid,
                "status": "active",
            }
        )
        cover = await self.stickers.find_one(
            {
                "pack_id": pack_oid,
                "status": "active",
            },
            sort=[("sort_order", 1), ("created_at", 1), ("_id", 1)],
        )

        updated = await self.packs.find_one_and_update(
            {"_id": pack_oid},
            {
                "$set": {
                    "sticker_count": sticker_count,
                    "cover_sticker_id": cover["_id"] if cover else None,
                    "updated_at": datetime.now(UTC),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)
        return updated

    async def list_joined_stickers_by_ids(self, *, sticker_ids: Iterable[str]) -> list[dict[str, Any]]:
        object_ids: list[ObjectId] = []
        for sticker_id in sticker_ids:
            object_ids.append(_oid(sticker_id))

        if not object_ids:
            return []

        pipeline = [
            {"$match": {"_id": {"$in": object_ids}}},
            {
                "$lookup": {
                    "from": COL_STICKER_PACKS,
                    "localField": "pack_id",
                    "foreignField": "_id",
                    "as": "pack",
                }
            },
            {"$unwind": "$pack"},
        ]
        return await self.stickers.aggregate(pipeline).to_list(length=len(object_ids))

    async def user_has_message_access_to_sticker(self, *, sticker_id: str, user_id: str) -> bool:
        doc = await self.messages.find_one(
            {
                "type": "sticker",
                "sticker.sticker_id": sticker_id,
                "hidden_for_user_ids": {"$ne": user_id},
                "$or": [
                    {"sender_id": _oid(user_id)},
                    {"receiver_id": _oid(user_id)},
                ],
            },
            {"_id": 1},
        )
        return doc is not None
