from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator
from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_SPACES
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class SpaceDocument(TimestampedDocument):
    """Explicit resource container layer owning Groups and Channels."""

    name: str
    slug: str
    description: str | None = None
    avatar: dict[str, Any] | None = None
    banner: dict[str, Any] | None = None
    owner_user_id: StrId
    created_by: StrId
    visibility: Literal["private", "public"] = "private"
    join_policy: Literal["open", "approval", "invite_only", "closed"] = "open"
    default_role_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _default_owner_user_id(cls, data: Any) -> Any:
        """Seed ``owner_user_id`` from ``created_by`` when absent.

        Must run *before* field validation: ``owner_user_id`` is required, so an ``after``
        validator never gets the chance to fill it. This is what keeps pre-reshape space
        rows (which have no ``owner_user_id``) readable.
        """
        if isinstance(data, dict) and not data.get("owner_user_id") and data.get("created_by"):
            data = {**data, "owner_user_id": data["created_by"]}
        return data

    @property
    def kind(self) -> str:
        """Legacy ``workspace|community`` discriminator, now folded into ``settings``.

        Compatibility view only; the target model has no first-class space kind.
        """
        return str(self.settings.get("kind", "workspace"))

    class Settings:
        name = COL_SPACES
        indexes = [
            IndexModel([("slug", ASCENDING)], unique=True, name="ux_spaces_slug"),
            IndexModel([("owner_user_id", ASCENDING)], name="ix_spaces_owner_user_id"),
        ]
