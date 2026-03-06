from __future__ import annotations

from typing import Any, Optional

from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.api_models import SuccessResponse, Meta


def ok(
    request: Request,
    data: Any,
    *,
    meta: Optional[Meta] = None,
    status_code: int = 200,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    payload = SuccessResponse(data=data, meta=meta, request_id=request_id).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=payload)