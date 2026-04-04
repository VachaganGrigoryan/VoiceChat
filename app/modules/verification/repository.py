from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import AppError
from app.db.indexes import COL_VERIFICATION


def _oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise AppError(code="INVALID_ID", message="Invalid id", status_code=400)


class VerificationCodesRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db[COL_VERIFICATION]

    async def create_code(
        self,
        *,
        method: str,
        identifier: str,
        user_id: str,
        purpose: str,
        code_hash: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        doc = {
            "method": method,
            "identifier": identifier.lower().strip(),
            "user_id": user_id,  # keep as str for simplicity
            "purpose": purpose,
            "code_hash": code_hash,
            "attempts": 0,
            "expires_at": expires_at,
            "created_at": datetime.now(UTC),
        }
        res = await self.col.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def find_active_by_identifier_any(
        self,
        *,
        method: str,
        identifier: str,
        purposes: list[str],
    ) -> Optional[dict[str, Any]]:
        now = datetime.now(UTC)
        return await self.col.find_one(
            {
                "method": method,
                "identifier": identifier.lower().strip(),
                "purpose": {"$in": purposes},
                "expires_at": {"$gt": now},
            },
            sort=[("created_at", -1)],
        )

    async def increment_attempts(self, code_id: str) -> int:
        res = await self.col.find_one_and_update(
            {"_id": _oid(code_id)},
            {"$inc": {"attempts": 1}},
            return_document=True,
        )
        if not res:
            raise AppError(code="CODE_NOT_FOUND", message="Verification code not found", status_code=404)
        return int(res.get("attempts", 0))

    async def delete_by_user_method_and_purpose(
        self,
        *,
        user_id: str,
        method: str,
        purpose: str,
    ) -> None:
        await self.col.delete_many(
            {
                "user_id": user_id,
                "method": method,
                "purpose": purpose,
            }
        )
