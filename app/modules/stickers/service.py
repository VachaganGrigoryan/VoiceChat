from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

from bson import ObjectId

from app.core.config import settings
from app.core.errors import AppError
from app.infra.storage import get_storage
from app.modules.stickers.assets import StickerAssetMapper
from app.modules.stickers.repository import StickersRepository
from app.modules.stickers.schemas import (
    CompleteStickerUploadRequest,
    CreateStickerPackRequest,
    ResolvedStickerItem,
    ResolveStickersResponse,
    StickerCatalogItem,
    StickerPackDetail,
    StickerPackSummary,
    StickerUploadTargetResponse,
    UpdateStickerPackRequest,
    UpdateStickerRequest,
)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
ALLOWED_STICKER_CONTENT_TYPES = {"image/webp"}


def _id_str(value: Any) -> str:
    if isinstance(value, ObjectId):
        return str(value)
    return str(value)


class StickersService:
    def __init__(self, repo: StickersRepository):
        self.repo = repo
        self.assets = StickerAssetMapper()

    async def create_pack(self, *, owner_user_id: str, body: CreateStickerPackRequest) -> StickerPackSummary:
        pack = await self.repo.create_pack(
            owner_user_id=owner_user_id,
            slug=self._normalize_slug(body.slug, field_name="slug"),
            title=self._normalize_required_text(body.title, field_name="title"),
            description=self._normalize_optional_text(body.description),
            visibility=body.visibility,
            tags=self._normalize_tags(body.tags),
        )
        return self._to_pack_summary(pack)

    async def list_my_packs(self, *, owner_user_id: str) -> list[StickerPackSummary]:
        packs = await self.repo.list_owner_packs(owner_user_id=owner_user_id)
        return [self._to_pack_summary(pack) for pack in packs]

    async def get_pack(self, *, owner_user_id: str, pack_id: str) -> StickerPackDetail:
        pack = await self.repo.get_pack_for_owner(pack_id=pack_id, owner_user_id=owner_user_id)
        if not pack:
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)

        stickers = await self.repo.list_pack_stickers(pack_id=pack_id)
        items = [self._to_catalog_item(sticker, pack=pack) for sticker in stickers]
        return StickerPackDetail(**self._to_pack_summary(pack).model_dump(), stickers=items)

    async def update_pack(
        self,
        *,
        owner_user_id: str,
        pack_id: str,
        body: UpdateStickerPackRequest,
    ) -> StickerPackSummary:
        updates: dict[str, Any] = {}
        if body.slug is not None:
            updates["slug"] = self._normalize_slug(body.slug, field_name="slug")
        if body.title is not None:
            updates["title"] = self._normalize_required_text(body.title, field_name="title")
        if body.description is not None:
            updates["description"] = self._normalize_optional_text(body.description)
        if body.visibility is not None:
            updates["visibility"] = body.visibility
        if body.tags is not None:
            updates["tags"] = self._normalize_tags(body.tags)

        pack = await self.repo.update_pack(pack_id=pack_id, owner_user_id=owner_user_id, updates=updates)
        return self._to_pack_summary(pack)

    async def publish_pack(self, *, owner_user_id: str, pack_id: str) -> StickerPackSummary:
        pack = await self.repo.get_pack_for_owner(pack_id=pack_id, owner_user_id=owner_user_id)
        if not pack:
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)
        if int(pack.get("sticker_count", 0)) < 1:
            raise AppError(
                code="STICKER_PACK_EMPTY",
                message="Sticker pack must contain at least one active sticker before publishing",
                status_code=400,
            )
        if pack.get("status") == "active":
            return self._to_pack_summary(pack)

        updated = await self.repo.publish_pack(pack_id=pack_id, owner_user_id=owner_user_id)
        return self._to_pack_summary(updated)

    async def delete_pack(self, *, owner_user_id: str, pack_id: str) -> StickerPackSummary:
        pack = await self.repo.soft_delete_pack(pack_id=pack_id, owner_user_id=owner_user_id)
        return self._to_pack_summary(pack)

    async def request_upload(
        self,
        *,
        owner_user_id: str,
        pack_id: str,
        filename: str,
        content_type: str,
        expected_size: int,
    ) -> StickerUploadTargetResponse:
        pack = await self.repo.get_pack_for_owner(pack_id=pack_id, owner_user_id=owner_user_id)
        if not pack:
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)
        if pack.get("status") == "archived":
            raise AppError(code="STICKER_PACK_ARCHIVED", message="Sticker pack is archived", status_code=400)

        normalized_type = (content_type or "").lower().strip()
        if normalized_type not in ALLOWED_STICKER_CONTENT_TYPES:
            raise AppError(
                code="UNSUPPORTED_STICKER_TYPE",
                message="Sticker must be uploaded as image/webp",
                status_code=415,
            )
        if expected_size > settings.sticker_max_bytes:
            raise AppError(
                code="STICKER_FILE_TOO_LARGE",
                message="Sticker exceeds the max allowed size",
                status_code=413,
            )

        session_id = ObjectId()
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.sticker_upload_session_ttl_seconds)
        session_id_str = str(session_id)
        storage_key = f"stickers/tmp/{owner_user_id}/{session_id_str}.webp"
        storage = get_storage()
        session = await self.repo.create_upload_session(
            session_id=session_id,
            owner_user_id=owner_user_id,
            pack_id=pack_id,
            storage=storage.name,
            filename=filename.strip(),
            content_type=normalized_type,
            expected_size=expected_size,
            storage_key=storage_key,
            expires_at=expires_at,
        )

        upload_target = storage.create_upload_target(
            key=storage_key,
            mime=normalized_type,
            expires_in=settings.sticker_upload_presign_expires_seconds,
        )
        if upload_target is None:
            upload_url = f"/stickers/uploads/{session_id_str}/content"
            upload_method = "PUT"
            upload_headers: dict[str, str] = {"Content-Type": normalized_type}
        else:
            upload_url = upload_target.url
            upload_method = upload_target.method
            upload_headers = upload_target.headers

        return StickerUploadTargetResponse(
            upload_session_id=str(session["_id"]),
            upload_url=upload_url,
            upload_method=upload_method,
            upload_headers=upload_headers,
            expires_at=session["expires_at"],
        )

    async def upload_local_content(
        self,
        *,
        owner_user_id: str,
        upload_session_id: str,
        content: bytes,
        content_type: str,
    ) -> None:
        session = await self._get_upload_session(owner_user_id=owner_user_id, upload_session_id=upload_session_id)
        if session.get("status") == "completed":
            raise AppError(
                code="STICKER_UPLOAD_ALREADY_COMPLETED",
                message="Upload session is already completed",
                status_code=409,
            )

        normalized_type = (content_type or "").lower().strip()
        if normalized_type != session["content_type"]:
            raise AppError(
                code="STICKER_UPLOAD_CONTENT_TYPE_MISMATCH",
                message="Uploaded content type does not match the upload session",
                status_code=400,
            )
        if len(content) != int(session["expected_size"]):
            raise AppError(
                code="STICKER_UPLOAD_SIZE_MISMATCH",
                message="Uploaded file size does not match the upload session",
                status_code=400,
            )
        if len(content) > settings.sticker_max_bytes:
            raise AppError(
                code="STICKER_FILE_TOO_LARGE",
                message="Sticker exceeds the max allowed size",
                status_code=413,
            )

        storage = get_storage(provider=self.assets.resolve_storage_name(session))
        await storage.save(
            filename=session["filename"],
            content=content,
            mime=session["content_type"],
            key=session["storage_key"],
        )
        await self.repo.update_upload_session_status(
            upload_session_id=upload_session_id,
            owner_user_id=owner_user_id,
            status="uploaded",
        )

    async def complete_upload(
        self,
        *,
        owner_user_id: str,
        upload_session_id: str,
        body: CompleteStickerUploadRequest,
    ) -> StickerCatalogItem:
        session = await self._get_upload_session(owner_user_id=owner_user_id, upload_session_id=upload_session_id)
        if session.get("status") == "completed":
            raise AppError(
                code="STICKER_UPLOAD_ALREADY_COMPLETED",
                message="Upload session is already completed",
                status_code=409,
            )

        pack = await self.repo.get_pack_for_owner(
            pack_id=str(session["pack_id"]),
            owner_user_id=owner_user_id,
        )
        if not pack:
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)

        storage = get_storage(provider=self.assets.resolve_storage_name(session))
        try:
            source_bytes = await storage.read(session["storage_key"])
        except FileNotFoundError as exc:
            raise AppError(
                code="STICKER_UPLOAD_NOT_FOUND",
                message="Uploaded sticker file was not found",
                status_code=404,
            ) from exc

        if len(source_bytes) != int(session["expected_size"]):
            raise AppError(
                code="STICKER_UPLOAD_SIZE_MISMATCH",
                message="Uploaded file size does not match the upload session",
                status_code=400,
            )

        processed = self._process_sticker_bytes(source_bytes)
        sticker_id = ObjectId()
        sticker_id_str = str(sticker_id)
        pack_id_str = str(pack["_id"])
        original_key = f"stickers/{pack_id_str}/{sticker_id_str}/original.webp"
        thumb_key = f"stickers/{pack_id_str}/{sticker_id_str}/thumb.webp"

        original_saved = None
        thumb_saved = None
        try:
            original_saved = await storage.save(
                filename="original.webp",
                content=processed["original_bytes"],
                mime="image/webp",
                key=original_key,
            )
            thumb_saved = await storage.save(
                filename="thumb.webp",
                content=processed["thumb_bytes"],
                mime="image/webp",
                key=thumb_key,
            )
            if original_saved.storage != thumb_saved.storage:
                raise AppError(
                    code="STICKER_STORAGE_MISMATCH",
                    message="Sticker assets must be stored in the same storage backend",
                    status_code=500,
                )

            sort_order = body.sort_order
            if sort_order is None:
                sort_order = await self.repo.next_sticker_sort_order(pack_id=pack_id_str)

            sticker = await self.repo.create_sticker(
                sticker_id=sticker_id,
                pack_id=pack_id_str,
                created_by_user_id=owner_user_id,
                storage=original_saved.storage,
                slug=self._normalize_slug(body.slug, field_name="slug"),
                title=self._normalize_required_text(body.title, field_name="title"),
                emoji_aliases=self._normalize_emoji_aliases(body.emoji_aliases),
                storage_key=original_saved.key,
                thumbnail_storage_key=thumb_saved.key,
                width=processed["width"],
                height=processed["height"],
                file_size=len(processed["original_bytes"]),
                checksum_sha256=processed["checksum_sha256"],
                sort_order=sort_order,
            )
            pack = await self.repo.refresh_pack_stats(pack_id=pack_id_str)
            await self.repo.update_upload_session_status(
                upload_session_id=upload_session_id,
                owner_user_id=owner_user_id,
                status="completed",
                completed=True,
            )
        except Exception:
            if original_saved is not None:
                await storage.delete(original_saved.key)
            if thumb_saved is not None:
                await storage.delete(thumb_saved.key)
            raise
        finally:
            await storage.delete(session["storage_key"])

        return self._to_catalog_item(sticker, pack=pack)

    async def update_sticker(
        self,
        *,
        owner_user_id: str,
        sticker_id: str,
        body: UpdateStickerRequest,
    ) -> StickerCatalogItem:
        joined = await self.repo.get_joined_sticker_for_owner(sticker_id=sticker_id, owner_user_id=owner_user_id)
        if not joined or joined["pack"].get("is_deleted"):
            raise AppError(code="STICKER_NOT_FOUND", message="Sticker not found", status_code=404)

        updates: dict[str, Any] = {}
        if body.slug is not None:
            updates["slug"] = self._normalize_slug(body.slug, field_name="slug")
        if body.title is not None:
            updates["title"] = self._normalize_required_text(body.title, field_name="title")
        if body.emoji_aliases is not None:
            updates["emoji_aliases"] = self._normalize_emoji_aliases(body.emoji_aliases)
        if body.sort_order is not None:
            updates["sort_order"] = body.sort_order
        if body.status is not None:
            updates["status"] = body.status

        if updates:
            updates["version"] = int(joined.get("version", 1)) + 1

        sticker = await self.repo.update_sticker(sticker_id=sticker_id, updates=updates)
        pack = joined["pack"]
        if {"status", "sort_order"} & updates.keys():
            pack = await self.repo.refresh_pack_stats(pack_id=str(pack["_id"]))
        return self._to_catalog_item(sticker, pack=pack)

    async def delete_sticker(self, *, owner_user_id: str, sticker_id: str) -> StickerCatalogItem:
        joined = await self.repo.get_joined_sticker_for_owner(sticker_id=sticker_id, owner_user_id=owner_user_id)
        if not joined or joined["pack"].get("is_deleted"):
            raise AppError(code="STICKER_NOT_FOUND", message="Sticker not found", status_code=404)

        sticker = await self.repo.update_sticker(
            sticker_id=sticker_id,
            updates={
                "status": "archived",
                "version": int(joined.get("version", 1)) + 1,
            },
        )
        pack = await self.repo.refresh_pack_stats(pack_id=str(joined["pack"]["_id"]))
        return self._to_catalog_item(sticker, pack=pack)

    async def resolve_stickers(self, *, user_id: str, sticker_ids: list[str]) -> ResolveStickersResponse:
        if not sticker_ids:
            return ResolveStickersResponse(items=[])

        rows = await self.repo.list_joined_stickers_by_ids(sticker_ids=sticker_ids)
        rows_by_id = {str(row["_id"]): row for row in rows}
        items: list[ResolvedStickerItem] = []

        for sticker_id in sticker_ids:
            row = rows_by_id.get(sticker_id)
            if row is None:
                continue

            pack = row["pack"]
            is_owner = _id_str(pack["owner_user_id"]) == user_id
            if not is_owner:
                has_access = await self.repo.user_has_message_access_to_sticker(sticker_id=sticker_id, user_id=user_id)
                if not has_access:
                    continue

            asset_urls = self.assets.to_catalog_urls(row)
            items.append(
                ResolvedStickerItem(
                    sticker_id=sticker_id,
                    cdn_url=asset_urls["cdn_url"],
                    thumb_url=asset_urls["thumb_url"],
                    width=int(row["width"]),
                    height=int(row["height"]),
                    is_animated=bool(row.get("is_animated", False)),
                    version=int(row.get("version", 1)),
                )
            )

        return ResolveStickersResponse(items=items)

    async def get_message_sticker_media_map(self, *, sticker_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not sticker_ids:
            return {}

        rows = await self.repo.list_joined_stickers_by_ids(sticker_ids=sticker_ids)
        return {str(row["_id"]): self.assets.to_message_media(row) for row in rows}

    async def search_by_emoji(self, *, owner_user_id: str, emoji: str) -> list[StickerCatalogItem]:
        normalized = self._normalize_emoji(emoji)
        rows = await self.repo.search_joined_stickers_by_emoji(owner_user_id=owner_user_id, emoji=normalized)
        return [self._to_catalog_item(row, pack=row["pack"]) for row in rows]

    async def get_by_ref(self, *, owner_user_id: str, pack_slug: str, sticker_slug: str) -> StickerCatalogItem:
        row = await self.repo.get_joined_sticker_by_ref_for_owner(
            pack_slug=self._normalize_slug(pack_slug, field_name="pack_slug"),
            sticker_slug=self._normalize_slug(sticker_slug, field_name="sticker_slug"),
            owner_user_id=owner_user_id,
        )
        if row is None:
            raise AppError(code="STICKER_NOT_FOUND", message="Sticker not found", status_code=404)
        return self._to_catalog_item(row, pack=row["pack"])

    async def prepare_message_sticker(
        self,
        *,
        owner_user_id: str,
        sticker_id: str,
        emoji: str | None = None,
    ) -> dict[str, Any]:
        row = await self.repo.get_joined_sticker_for_owner(sticker_id=sticker_id, owner_user_id=owner_user_id)
        if row is None:
            raise AppError(code="STICKER_NOT_FOUND", message="Sticker not found", status_code=404)

        pack = row["pack"]
        if pack.get("is_deleted"):
            raise AppError(code="STICKER_PACK_NOT_FOUND", message="Sticker pack not found", status_code=404)
        if pack.get("status") != "active":
            raise AppError(
                code="STICKER_PACK_NOT_PUBLISHED",
                message="Sticker pack must be published before sending stickers",
                status_code=400,
            )
        if row.get("status") != "active":
            raise AppError(
                code="STICKER_NOT_ACTIVE",
                message="Sticker is not active",
                status_code=400,
            )

        normalized_emoji = None
        if emoji is not None:
            normalized_emoji = self._normalize_emoji(emoji)
            if normalized_emoji not in row.get("emoji_aliases", []):
                raise AppError(
                    code="INVALID_STICKER_EMOJI",
                    message="Emoji must match one of the sticker emoji aliases",
                    status_code=400,
                )
        elif row.get("emoji_aliases"):
            normalized_emoji = row["emoji_aliases"][0]

        return {
            "sticker_id": str(row["_id"]),
            "pack_id": _id_str(row["pack_id"]),
            "pack_slug": pack["slug"],
            "sticker_slug": row["slug"],
            "emoji": normalized_emoji,
            "version": int(row.get("version", 1)),
        }

    def _normalize_slug(self, value: str, *, field_name: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            raise AppError(code="INVALID_SLUG", message=f"{field_name} is required", status_code=400)
        if not SLUG_RE.fullmatch(normalized):
            raise AppError(
                code="INVALID_SLUG",
                message=f"{field_name} must match ^[a-z0-9]+(?:_[a-z0-9]+)*$",
                status_code=400,
            )
        return normalized

    def _normalize_required_text(self, value: str, *, field_name: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise AppError(code="INVALID_INPUT", message=f"{field_name} is required", status_code=400)
        return normalized

    def _normalize_optional_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _normalize_tags(self, tags: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            value = (tag or "").strip().lower()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _normalize_emoji_aliases(self, emoji_aliases: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for emoji in emoji_aliases:
            value = self._normalize_emoji(emoji)
            if value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _normalize_emoji(self, emoji: str) -> str:
        normalized = (emoji or "").strip()
        if not normalized:
            raise AppError(code="INVALID_EMOJI", message="emoji is required", status_code=400)
        return normalized

    def _process_sticker_bytes(self, source_bytes: bytes) -> dict[str, Any]:
        if len(source_bytes) > settings.sticker_max_bytes:
            raise AppError(
                code="STICKER_FILE_TOO_LARGE",
                message="Sticker exceeds the max allowed size",
                status_code=413,
            )

        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError as exc:
            raise AppError(
                code="STICKER_PROCESSING_UNAVAILABLE",
                message="Pillow is required for sticker uploads",
                status_code=500,
            ) from exc

        try:
            with Image.open(BytesIO(source_bytes)) as image:
                if image.format != "WEBP":
                    raise AppError(
                        code="UNSUPPORTED_STICKER_TYPE",
                        message="Sticker must be uploaded as image/webp",
                        status_code=415,
                    )
                if getattr(image, "is_animated", False):
                    raise AppError(
                        code="ANIMATED_STICKERS_NOT_SUPPORTED",
                        message="Animated stickers are not supported in v1",
                        status_code=400,
                    )

                width, height = image.size
                if width > settings.sticker_max_width or height > settings.sticker_max_height:
                    raise AppError(
                        code="STICKER_DIMENSIONS_EXCEEDED",
                        message="Sticker dimensions exceed the maximum allowed size",
                        status_code=400,
                    )

                original_image = image.convert("RGBA")
                original_buffer = BytesIO()
                original_image.save(original_buffer, format="WEBP", method=6)
                original_bytes = original_buffer.getvalue()

                thumb_image = original_image.copy()
                thumb_image.thumbnail(
                    (settings.sticker_thumb_max_side, settings.sticker_thumb_max_side),
                    Image.Resampling.LANCZOS,
                )
                thumb_buffer = BytesIO()
                thumb_image.save(thumb_buffer, format="WEBP", method=6)
                thumb_bytes = thumb_buffer.getvalue()
        except UnidentifiedImageError as exc:
            raise AppError(
                code="INVALID_STICKER_FILE",
                message="Sticker file is invalid",
                status_code=400,
            ) from exc

        if len(original_bytes) > settings.sticker_max_bytes:
            raise AppError(
                code="STICKER_FILE_TOO_LARGE",
                message="Sticker exceeds the max allowed size",
                status_code=413,
            )

        return {
            "original_bytes": original_bytes,
            "thumb_bytes": thumb_bytes,
            "width": width,
            "height": height,
            "checksum_sha256": hashlib.sha256(original_bytes).hexdigest(),
        }

    async def _get_upload_session(self, *, owner_user_id: str, upload_session_id: str) -> dict[str, Any]:
        session = await self.repo.get_upload_session(upload_session_id=upload_session_id, owner_user_id=owner_user_id)
        if session is None:
            raise AppError(
                code="STICKER_UPLOAD_SESSION_NOT_FOUND",
                message="Upload session not found",
                status_code=404,
            )
        expires_at = session["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise AppError(
                code="STICKER_UPLOAD_SESSION_EXPIRED",
                message="Upload session has expired",
                status_code=410,
            )
        return session

    def _to_pack_summary(self, pack: dict[str, Any]) -> StickerPackSummary:
        cover_sticker_id = pack.get("cover_sticker_id")
        return StickerPackSummary(
            id=str(pack["_id"]),
            slug=pack["slug"],
            owner_user_id=_id_str(pack["owner_user_id"]),
            title=pack["title"],
            description=pack.get("description"),
            cover_sticker_id=_id_str(cover_sticker_id) if cover_sticker_id else None,
            visibility=pack.get("visibility", "private"),
            status=pack.get("status", "draft"),
            kind=pack.get("kind", "custom"),
            tags=list(pack.get("tags", [])),
            sticker_count=int(pack.get("sticker_count", 0)),
            is_deleted=bool(pack.get("is_deleted", False)),
            created_at=pack["created_at"],
            updated_at=pack["updated_at"],
            published_at=pack.get("published_at"),
        )

    def _to_catalog_item(self, sticker: dict[str, Any], *, pack: dict[str, Any]) -> StickerCatalogItem:
        asset_urls = self.assets.to_catalog_urls(sticker)
        return StickerCatalogItem(
            id=str(sticker["_id"]),
            pack_id=_id_str(sticker["pack_id"]),
            pack_slug=pack["slug"],
            slug=sticker["slug"],
            title=sticker["title"],
            emoji_aliases=list(sticker.get("emoji_aliases", [])),
            file_kind=sticker.get("file_kind", "webp"),
            mime_type=sticker.get("mime_type", "image/webp"),
            width=int(sticker["width"]),
            height=int(sticker["height"]),
            file_size=int(sticker["file_size"]),
            checksum_sha256=sticker["checksum_sha256"],
            status=sticker.get("status", "active"),
            sort_order=int(sticker.get("sort_order", 0)),
            version=int(sticker.get("version", 1)),
            is_animated=bool(sticker.get("is_animated", False)),
            created_by_user_id=_id_str(sticker["created_by_user_id"]),
            created_at=sticker["created_at"],
            updated_at=sticker["updated_at"],
            cdn_url=asset_urls["cdn_url"],
            thumb_url=asset_urls["thumb_url"],
        )
