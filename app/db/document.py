from __future__ import annotations

from datetime import UTC, datetime

from beanie import Document, Insert, Replace, SaveChanges, Update, before_event
from pydantic import BaseModel, ConfigDict, Field


class EmbeddedBase(BaseModel):
    """Shared base for embedded (sub-document) pydantic models.

    Mirrors `BaseDocument`'s config so nested values accept native objects
    (datetime, ObjectId) and tolerate extra persisted fields.
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class BaseDocument(Document):
    """Shared base for all Beanie documents.

    `extra="allow"` preserves behaviour where a few repositories persist fields
    that aren't declared on the model (e.g. fields from legacy documents).
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    @property
    def str_id(self) -> str:
        """The document id as a plain string ("" when unsaved).

        Use this instead of `doc.id or ""`: `id` is a `PydanticObjectId`, which is
        truthy, so `or ""` would NOT stringify it.
        """
        return str(self.id) if self.id is not None else ""


class TimestampedDocument(BaseDocument):
    """Base for documents carrying `created_at`/`updated_at`.

    `updated_at` is stamped automatically on document-level writes (`insert`,
    `replace`, `save`, `save_changes`, instance `set`/`update`). NOTE: query-level
    bulk writes (`Document.find(...).update(...)`) do NOT fire Beanie events, so
    those call sites must set `updated_at` explicitly.
    """

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @before_event(Insert)
    def _stamp_on_insert(self) -> None:
        now = datetime.now(UTC)
        self.updated_at = now

    @before_event([Replace, SaveChanges, Update])
    def _stamp_on_update(self) -> None:
        self.updated_at = datetime.now(UTC)
