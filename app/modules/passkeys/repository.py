from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from beanie.operators import Set

from app.db.models import PasskeyChallengeDocument, PasskeyDocument
from app.db.repository import BaseRepository


class PasskeysRepository(BaseRepository[PasskeyDocument]):
    model = PasskeyDocument

    async def create_passkey(self, doc: dict[str, Any]) -> PasskeyDocument:
        passkey = PasskeyDocument(**doc)
        await passkey.insert()
        return passkey

    async def find_by_credential_id(self, credential_id: str) -> PasskeyDocument | None:
        return await PasskeyDocument.find_one(
            PasskeyDocument.credential_id == credential_id
        )

    async def list_by_user_id(self, user_id: str) -> list[PasskeyDocument]:
        return (
            await PasskeyDocument.find(PasskeyDocument.user_id == user_id)
            .sort("-created_at")
            .to_list()
        )

    async def delete_by_credential_id(self, user_id: str, credential_id: str) -> bool:
        result = await PasskeyDocument.find(
            PasskeyDocument.user_id == user_id,
            PasskeyDocument.credential_id == credential_id,
        ).delete()
        return bool(result and result.deleted_count > 0)

    async def update_sign_count(
        self, credential_id: str, sign_count: int, now: datetime
    ) -> None:
        await PasskeyDocument.find(
            PasskeyDocument.credential_id == credential_id
        ).update(
            Set(
                {
                    PasskeyDocument.sign_count: sign_count,
                    PasskeyDocument.last_used_at: now,
                    PasskeyDocument.updated_at: now,
                }
            )
        )

    async def count_by_user_id(self, user_id: str) -> int:
        return await PasskeyDocument.find(PasskeyDocument.user_id == user_id).count()


class PasskeyChallengesRepository(BaseRepository[PasskeyChallengeDocument]):
    model = PasskeyChallengeDocument

    async def create_challenge(
        self,
        *,
        flow: Literal["register", "authenticate"],
        challenge: str,
        expires_at: datetime,
        user_id: str | None = None,
        email: str | None = None,
        now: datetime,
    ) -> PasskeyChallengeDocument:
        doc = PasskeyChallengeDocument(
            flow=flow,
            challenge=challenge,
            expires_at=expires_at,
            created_at=now,
            user_id=user_id,
            email=email,
        )
        await doc.insert()
        return doc

    async def consume_active_challenge(
        self,
        *,
        flow: Literal["register", "authenticate"],
        challenge: str,
        now: datetime,
        user_id: str | None = None,
        email: str | None = None,
    ) -> PasskeyChallengeDocument | None:
        # Atomic claim (prevents challenge replay) via the raw async collection.
        query: dict[str, Any] = {
            "flow": flow,
            "challenge": challenge,
            "used_at": None,
            "expires_at": {"$gt": now},
        }
        if user_id is not None:
            query["user_id"] = user_id
        if email is not None:
            query["email"] = email

        return await self.find_one_and_update(query, {"$set": {"used_at": now}})
