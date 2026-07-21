from __future__ import annotations

from typing import Any, Literal

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_SPACES
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class SpaceDocument(TimestampedDocument):
    """Optional workspace/organization grouping layer.

    Spaces are opt-in: users and conversations remain fully functional with no
    space (global scope). Conversations link to a space via a nullable
    ``space_id``. Seam capability (``chat-spaces``); deep administration deferred.
    """

    name: str
    slug: str
    kind: Literal["workspace", "community"] = "workspace"
    created_by: StrId
    avatar: dict[str, Any] | None = None
    visibility: Literal["private", "public"] = "private"
    settings: dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = COL_SPACES
        indexes = [
            IndexModel([("slug", ASCENDING)], unique=True, name="ux_spaces_slug"),
        ]
