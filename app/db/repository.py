from __future__ import annotations

from typing import Any

from pymongo import ReturnDocument

from app.core.errors import AppError
from app.db.document import BaseDocument
from app.db.object_id import parse_object_id


class BaseRepository[TDoc: BaseDocument]:
    """Shared contract for Beanie-backed repositories.

    Subclasses set `model` to their Document type and inherit id lookup, 404
    fetching, raw-collection access, and atomic find-and-modify — the helpers
    that were previously duplicated across every repository.
    """

    model: type[TDoc]

    async def get_by_id(
        self, id: str, *, invalid_message: str = "Invalid id"
    ) -> TDoc | None:
        return await self.model.get(parse_object_id(id, message=invalid_message))

    async def get_or_404(
        self,
        id: str,
        *,
        code: str,
        message: str,
        invalid_message: str = "Invalid id",
    ) -> TDoc:
        doc = await self.get_by_id(id, invalid_message=invalid_message)
        if doc is None:
            raise AppError(code=code, message=message, status_code=404)
        return doc

    @property
    def raw(self) -> Any:
        """Raw async pymongo collection for Mongo-shaped operations that Beanie's
        query API doesn't cover: aggregation, dotted `$set`, atomic
        find-and-modify (CAS), and bulk writes."""
        return self.model.get_pymongo_collection()

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any]
    ) -> TDoc | None:
        """Atomic find-and-modify returning the post-update document."""
        raw = await self.raw.find_one_and_update(
            query, update, return_document=ReturnDocument.AFTER
        )
        return self.model.model_validate(raw) if raw is not None else None
