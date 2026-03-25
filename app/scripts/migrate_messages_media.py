from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.db.indexes import COL_MESSAGES

LEGACY_MEDIA_KIND_BY_TYPE = {
    "voice": "voice",
    "image": "image",
    "video": "video",
    "sticker": "image",
}


def _infer_media_kind_from_mime(mime: Any) -> str:
    if not isinstance(mime, str):
        return "file"

    normalized = mime.lower().strip()
    if normalized.startswith("audio/"):
        return "audio"
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    return "file"


def _normalize_media_payload(payload: dict[str, Any], *, kind: str) -> dict[str, Any]:
    duration_ms = payload.get("duration_ms")
    if duration_ms in (None, ""):
        normalized_duration_ms = None
    else:
        normalized_duration_ms = int(duration_ms)

    return {
        "kind": kind,
        "storage": payload.get("storage", "local"),
        "key": payload.get("key", ""),
        "url": payload.get("url", ""),
        "mime": payload.get("mime", "application/octet-stream"),
        "size_bytes": int(payload.get("size_bytes", 0) or 0),
        "duration_ms": normalized_duration_ms,
    }


def _normalize_message_type(message_type: Any, *, media_kind: str | None) -> str:
    if message_type == "text":
        return "text"
    if message_type == "emoji":
        return "text"
    if message_type in {"file"}:
        return "file"
    if message_type in {"media", "voice", "image", "video", "sticker"}:
        return "media"
    if media_kind == "file":
        return "file"
    if media_kind is not None:
        return "media"
    return "text"


def _normalize_reply_preview(reply_preview: Any) -> dict[str, Any] | None:
    if not isinstance(reply_preview, dict):
        return None

    preview_media_kind = reply_preview.get("media_kind")
    normalized_type = _normalize_message_type(
        reply_preview.get("type"),
        media_kind=preview_media_kind,
    )

    normalized_preview = {
        **reply_preview,
        "type": normalized_type,
    }
    if normalized_type == "file" and not normalized_preview.get("media_kind"):
        normalized_preview["media_kind"] = "file"

    return normalized_preview


def _resolve_media_kind(
    message: dict[str, Any], payload: dict[str, Any] | None
) -> str | None:
    message_type = message.get("type")
    legacy_kind = LEGACY_MEDIA_KIND_BY_TYPE.get(message_type)
    if legacy_kind is not None:
        return legacy_kind

    if message_type == "file":
        return "file"

    if isinstance(payload, dict):
        existing_kind = payload.get("kind")
        if isinstance(existing_kind, str) and existing_kind:
            return existing_kind
        return _infer_media_kind_from_mime(payload.get("mime"))

    return None


async def main() -> None:
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]
    col = db[COL_MESSAGES]

    total = 0
    updated = 0

    async for msg in col.find({"type": {"$ne": "text"}}):
        total += 1

        media_payload = None
        unset_data: dict[str, str] = {}

        if isinstance(msg.get("media"), dict):
            media_payload = msg["media"]
        elif isinstance(msg.get("audio"), dict):
            media_payload = msg["audio"]
            unset_data["audio"] = ""

        media_kind = _resolve_media_kind(msg, media_payload)
        normalized_type = _normalize_message_type(
            msg.get("type"), media_kind=media_kind
        )

        set_data: dict[str, Any] = {}

        if normalized_type != msg.get("type"):
            set_data["type"] = normalized_type

        if media_payload is not None and media_kind is not None:
            normalized_media = _normalize_media_payload(media_payload, kind=media_kind)
            if normalized_media != msg.get("media"):
                set_data["media"] = normalized_media

        normalized_reply_preview = _normalize_reply_preview(msg.get("reply_preview"))
        if (
            normalized_reply_preview is not None
            and normalized_reply_preview != msg.get("reply_preview")
        ):
            set_data["reply_preview"] = normalized_reply_preview

        if "text" not in msg:
            set_data["text"] = None
        if "edited_at" not in msg:
            set_data["edited_at"] = None
        if "status" not in msg or not msg.get("status"):
            set_data["status"] = "sent"

        if set_data or unset_data:
            set_data["updated_at"] = datetime.now(UTC)

            update_doc: dict[str, Any] = {"$set": set_data}
            if unset_data:
                update_doc["$unset"] = unset_data

            res = await col.update_one({"_id": msg["_id"]}, update_doc)
            if res.modified_count == 1:
                updated += 1
                print(f"updated message {msg['_id']}")

    print(f"scanned={total}, updated={updated}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
