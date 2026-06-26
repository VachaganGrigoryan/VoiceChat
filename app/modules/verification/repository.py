from __future__ import annotations

from datetime import UTC, datetime

from beanie.operators import In

from app.db.models import VerificationCodeDocument
from app.db.repository import BaseRepository


class VerificationCodesRepository(BaseRepository[VerificationCodeDocument]):
    model = VerificationCodeDocument

    async def create_code(
        self,
        *,
        method: str,
        identifier: str,
        user_id: str,
        purpose: str,
        code_hash: str,
        expires_at: datetime,
    ) -> VerificationCodeDocument:
        doc = VerificationCodeDocument(
            method=method,
            identifier=identifier.lower().strip(),
            user_id=user_id,
            purpose=purpose,
            code_hash=code_hash,
            expires_at=expires_at,
            created_at=datetime.now(UTC),
        )
        await doc.insert()
        return doc

    async def find_active_by_identifier_any(
        self,
        *,
        method: str,
        identifier: str,
        purposes: list[str],
    ) -> VerificationCodeDocument | None:
        now = datetime.now(UTC)
        return (
            await VerificationCodeDocument.find(
                VerificationCodeDocument.method == method,
                VerificationCodeDocument.identifier == identifier.lower().strip(),
                In(VerificationCodeDocument.purpose, purposes),
                VerificationCodeDocument.expires_at > now,
            )
            .sort("-created_at")
            .first_or_none()
        )

    async def increment_attempts(self, code_id: str) -> int:
        # Load-modify-save (not atomic); verification attempt counts are low-contention.
        doc = await self.get_or_404(
            code_id, code="CODE_NOT_FOUND", message="Verification code not found"
        )
        doc.attempts += 1
        await doc.save()
        return doc.attempts

    async def delete_by_user_method_and_purpose(
        self,
        *,
        user_id: str,
        method: str,
        purpose: str,
    ) -> None:
        await VerificationCodeDocument.find(
            VerificationCodeDocument.user_id == user_id,
            VerificationCodeDocument.method == method,
            VerificationCodeDocument.purpose == purpose,
        ).delete()
