from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.http.api_models import PaginationMeta, PaginatedResponse, SuccessResponse


def ok(
    request: Request,
    data: Any,
    *,
    status_code: int = 200,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    payload = SuccessResponse(
        data=data,
        request_id=request_id,
    ).model_dump(mode="json")

    return JSONResponse(status_code=status_code, content=payload)


def ok_paginated(
    request: Request,
    data: Any,
    *,
    meta: PaginationMeta,
    status_code: int = 200,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    payload = PaginatedResponse(
        data=data,
        meta=meta,
        request_id=request_id,
    ).model_dump(mode="json")

    return JSONResponse(status_code=status_code, content=payload)