from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

StickerPackVisibility = Literal["private", "shared", "public"]
StickerPackStatus = Literal["draft", "active", "blocked", "archived"]
StickerPackKind = Literal["system", "custom"]
StickerStatus = Literal["active", "blocked", "archived"]
StickerFileKind = Literal["webp"]
UploadSessionStatus = Literal["pending", "uploaded", "completed"]


class CreateStickerPackRequest(BaseModel):
    slug: str
    title: str = Field(min_length=1)
    description: str | None = None
    visibility: StickerPackVisibility = "private"
    tags: list[str] = Field(default_factory=list)


class UpdateStickerPackRequest(BaseModel):
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    visibility: StickerPackVisibility | None = None
    tags: list[str] | None = None


class StickerPackSummary(BaseModel):
    id: str
    slug: str
    owner_user_id: str
    title: str
    description: str | None = None
    cover_sticker_id: str | None = None
    visibility: StickerPackVisibility = "private"
    status: StickerPackStatus = "draft"
    kind: StickerPackKind = "custom"
    tags: list[str] = Field(default_factory=list)
    sticker_count: int = Field(default=0, ge=0)
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class StickerCatalogItem(BaseModel):
    id: str
    pack_id: str
    pack_slug: str
    slug: str
    title: str
    emoji_aliases: list[str] = Field(default_factory=list)
    file_kind: StickerFileKind = "webp"
    mime_type: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    file_size: int = Field(ge=0)
    checksum_sha256: str
    status: StickerStatus = "active"
    sort_order: int = Field(default=1, ge=0)
    version: int = Field(default=1, ge=1)
    is_animated: bool = False
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    cdn_url: str
    thumb_url: str


class StickerPackDetail(StickerPackSummary):
    stickers: list[StickerCatalogItem] = Field(default_factory=list)


class RequestStickerUploadRequest(BaseModel):
    filename: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    expected_size: int = Field(gt=0)


class StickerUploadTargetResponse(BaseModel):
    upload_session_id: str
    upload_url: str
    upload_method: str = "PUT"
    upload_headers: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime


class CompleteStickerUploadRequest(BaseModel):
    slug: str
    title: str = Field(min_length=1)
    emoji_aliases: list[str] = Field(default_factory=list)
    sort_order: int | None = Field(default=None, ge=0)


class UpdateStickerRequest(BaseModel):
    slug: str | None = None
    title: str | None = None
    emoji_aliases: list[str] | None = None
    sort_order: int | None = Field(default=None, ge=0)
    status: Literal["active", "blocked"] | None = None


class ResolveStickersRequest(BaseModel):
    sticker_ids: list[str] = Field(default_factory=list)


class ResolvedStickerItem(BaseModel):
    sticker_id: str
    cdn_url: str
    thumb_url: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    is_animated: bool = False
    version: int = Field(ge=1)


class ResolveStickersResponse(BaseModel):
    items: list[ResolvedStickerItem] = Field(default_factory=list)
