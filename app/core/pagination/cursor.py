from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from app.core.errors import AppError
from app.db.object_id import parse_object_id


def _normalize_cursor_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {
            "__type__": "datetime",
            "value": value.isoformat(),
        }
    return value


def _restore_cursor_value(value: Any) -> Any:
    if isinstance(value, dict) and value.get("__type__") == "datetime":
        return datetime.fromisoformat(value["value"])
    return value


def encode_cursor(**fields: Any) -> str:
    payload = {key: _normalize_cursor_value(value) for key, value in fields.items()}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def decode_cursor(cursor: str, *, required_fields: set[str] | None = None) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
        payload_raw = json.loads(raw)

        if not isinstance(payload_raw, dict):
            raise ValueError("cursor payload must be an object")

        payload = {
            key: _restore_cursor_value(value)
            for key, value in payload_raw.items()
        }

        if required_fields is not None:
            missing = required_fields - set(payload.keys())
            if missing:
                raise ValueError(f"missing cursor fields: {', '.join(sorted(missing))}")

        return payload
    except Exception as exc:
        raise AppError(
            code="INVALID_CURSOR",
            message="Invalid cursor",
            status_code=400,
        ) from exc


def keyset_condition(sort_field: str, cursor: dict[str, Any] | None) -> dict[str, Any]:
    """Keyset predicate on `(sort_field, _id)`, both sorted descending.

    Never an offset: results stay stable when a matching document is inserted
    between two page requests. Returns `{}` (no-op filter) for the first page.
    """
    if not cursor:
        return {}
    value = cursor.get("v")
    last_id = cursor.get("id")
    parsed_id = parse_object_id(str(last_id)) if last_id else None
    if value is None or parsed_id is None:
        return {}
    return {
        "$or": [
            {sort_field: {"$lt": value}},
            {sort_field: value, "_id": {"$lt": parsed_id}},
        ]
    }