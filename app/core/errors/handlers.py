from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors.exceptions import AppError
from app.core.http.api_models import ErrorResponse, ErrorEnvelope, ErrorDetail

log = logging.getLogger("app.errors")


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _json(status_code: int, code: str, message: str, request_id: str | None, details: Any = None) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorEnvelope(code=code, message=message, details=details),
        request_id=request_id,
    ).model_dump()
    return JSONResponse(status_code=status_code, content=payload)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _json(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=_request_id(request),
        details=exc.details,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details: list[dict[str, Any]] = []
    for e in exc.errors():
        loc = e.get("loc", [])
        # loc like ("body", "email") or ("query","limit")
        field = ".".join(str(x) for x in loc[1:]) if len(loc) > 1 else None
        details.append(
            ErrorDetail(
                field=field,
                message=e.get("msg", "Invalid value"),
                type=e.get("type"),
            ).model_dump()
        )

    return _json(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        request_id=_request_id(request),
        details=details,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # normalize FastAPI/Starlette HTTPException to our schema
    return _json(
        status_code=exc.status_code,
        code="HTTP_ERROR",
        message=str(exc.detail) if exc.detail else "HTTP error",
        request_id=_request_id(request),
        details=None,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # log with stacktrace
    log.exception("Unhandled exception", extra={"request_id": _request_id(request)})
    return _json(
        status_code=500,
        code="INTERNAL_ERROR",
        message="Internal server error",
        request_id=_request_id(request),
        details=None,
    )