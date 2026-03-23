from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.security import require_verified_user
from app.modules.stickers.dependencies import get_stickers_service
from app.modules.stickers.schemas import (
    CompleteStickerUploadRequest,
    CreateStickerPackRequest,
    RequestStickerUploadRequest,
    ResolveStickersRequest,
    ResolveStickersResponse,
    StickerCatalogItem,
    StickerPackDetail,
    StickerPackSummary,
    StickerUploadTargetResponse,
    UpdateStickerPackRequest,
    UpdateStickerRequest,
)
from app.modules.stickers.service import StickersService

router = APIRouter(
    prefix="/stickers",
    tags=["stickers"],
    responses=build_error_responses(400, 401, 403, 404, 409, 415, 422, 500),
)


@router.post(
    "/packs",
    status_code=201,
    response_model=SuccessResponse[StickerPackSummary],
)
async def create_pack(
    request: Request,
    body: CreateStickerPackRequest,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.create_pack(owner_user_id=str(user["_id"]), body=body)
    return ok(request, data=result, status_code=201)


@router.get(
    "/packs/my",
    response_model=SuccessResponse[list[StickerPackSummary]],
)
async def list_my_packs(
    request: Request,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.list_my_packs(owner_user_id=str(user["_id"]))
    return ok(request, data=result)


@router.get(
    "/packs/{pack_id}",
    response_model=SuccessResponse[StickerPackDetail],
)
async def get_pack(
    request: Request,
    pack_id: str,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.get_pack(owner_user_id=str(user["_id"]), pack_id=pack_id)
    return ok(request, data=result)


@router.patch(
    "/packs/{pack_id}",
    response_model=SuccessResponse[StickerPackSummary],
)
async def update_pack(
    request: Request,
    pack_id: str,
    body: UpdateStickerPackRequest,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.update_pack(owner_user_id=str(user["_id"]), pack_id=pack_id, body=body)
    return ok(request, data=result)


@router.post(
    "/packs/{pack_id}/publish",
    response_model=SuccessResponse[StickerPackSummary],
)
async def publish_pack(
    request: Request,
    pack_id: str,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.publish_pack(owner_user_id=str(user["_id"]), pack_id=pack_id)
    return ok(request, data=result)


@router.delete(
    "/packs/{pack_id}",
    response_model=SuccessResponse[StickerPackSummary],
)
async def delete_pack(
    request: Request,
    pack_id: str,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.delete_pack(owner_user_id=str(user["_id"]), pack_id=pack_id)
    return ok(request, data=result)


@router.post(
    "/packs/{pack_id}/stickers/upload",
    status_code=201,
    response_model=SuccessResponse[StickerUploadTargetResponse],
)
async def request_sticker_upload(
    request: Request,
    pack_id: str,
    body: RequestStickerUploadRequest,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.request_upload(
        owner_user_id=str(user["_id"]),
        pack_id=pack_id,
        filename=body.filename,
        content_type=body.content_type,
        expected_size=body.expected_size,
    )
    return ok(request, data=result, status_code=201)


@router.put("/uploads/{upload_session_id}/content", status_code=204)
async def upload_local_sticker_content(
    upload_session_id: str,
    request: Request,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    body = await request.body()
    await service.upload_local_content(
        owner_user_id=str(user["_id"]),
        upload_session_id=upload_session_id,
        content=body,
        content_type=request.headers.get("content-type", ""),
    )
    return Response(status_code=204)


@router.post(
    "/uploads/{upload_session_id}/complete",
    status_code=201,
    response_model=SuccessResponse[StickerCatalogItem],
)
async def complete_sticker_upload(
    request: Request,
    upload_session_id: str,
    body: CompleteStickerUploadRequest,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.complete_upload(
        owner_user_id=str(user["_id"]),
        upload_session_id=upload_session_id,
        body=body,
    )
    return ok(request, data=result, status_code=201)


@router.patch(
    "/{sticker_id}",
    response_model=SuccessResponse[StickerCatalogItem],
)
async def update_sticker(
    request: Request,
    sticker_id: str,
    body: UpdateStickerRequest,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.update_sticker(
        owner_user_id=str(user["_id"]),
        sticker_id=sticker_id,
        body=body,
    )
    return ok(request, data=result)


@router.delete(
    "/{sticker_id}",
    response_model=SuccessResponse[StickerCatalogItem],
)
async def delete_sticker(
    request: Request,
    sticker_id: str,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.delete_sticker(owner_user_id=str(user["_id"]), sticker_id=sticker_id)
    return ok(request, data=result)


@router.post(
    "/resolve",
    response_model=SuccessResponse[ResolveStickersResponse],
)
async def resolve_stickers(
    request: Request,
    body: ResolveStickersRequest,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.resolve_stickers(user_id=str(user["_id"]), sticker_ids=body.sticker_ids)
    return ok(request, data=result)


@router.get(
    "/search",
    response_model=SuccessResponse[list[StickerCatalogItem]],
)
async def search_stickers(
    request: Request,
    emoji: str = Query(..., min_length=1),
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.search_by_emoji(owner_user_id=str(user["_id"]), emoji=emoji)
    return ok(request, data=result)


@router.get(
    "/by-ref/{pack_slug}/{sticker_slug}",
    response_model=SuccessResponse[StickerCatalogItem],
)
async def get_sticker_by_ref(
    request: Request,
    pack_slug: str,
    sticker_slug: str,
    user: dict = Depends(require_verified_user),
    service: StickersService = Depends(get_stickers_service),
):
    result = await service.get_by_ref(
        owner_user_id=str(user["_id"]),
        pack_slug=pack_slug,
        sticker_slug=sticker_slug,
    )
    return ok(request, data=result)
