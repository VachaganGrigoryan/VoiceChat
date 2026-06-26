from __future__ import annotations

from typing import Annotated, Any

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BeforeValidator

from app.core.errors import AppError


def _coerce_id_str(value: Any) -> Any:
    # PydanticObjectId is an ObjectId subclass; normalize any (Pydantic)ObjectId to a
    # plain str so id fields never reject it (DB models *and* API response schemas).
    return str(value) if isinstance(value, ObjectId) else value


# A string id field that also accepts an ObjectId/PydanticObjectId (coerced to str).
StrId = Annotated[str, BeforeValidator(_coerce_id_str)]


def parse_object_id(value: str, *, message: str = "Invalid id") -> ObjectId:
    """Parse a string into an ObjectId, raising a 400 AppError on bad input."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise AppError(code="INVALID_ID", message=message, status_code=400) from exc
