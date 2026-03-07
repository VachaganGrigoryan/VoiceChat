from __future__ import annotations

import uuid
import json
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


SKIP_PATHS = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


class SuccessEnvelopeMiddleware(BaseHTTPMiddleware):
    """
    Wraps successful JSON responses into:
      { success: true, data: <original>, request_id }

    Only wraps:
      - HTTP 2xx
      - Content-Type includes application/json
    Skips:
      - non-JSON responses
      - already wrapped responses (has top-level "success")
    """

    async def dispatch(self, request: Request, call_next: Callable):
        # Skip swagger/openapi endpoints
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        response: Response = await call_next(request)

        # Only wrap successful responses
        if not (200 <= response.status_code <= 299):
            return response

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            return response

        # Read body (consume iterator)
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # Prepare headers for new response (IMPORTANT: remove content-length!)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("transfer-encoding", None)  # just in case

        request_id = getattr(request.state, "request_id", None)

        if not body:
            payload = {"success": True, "data": None, "request_id": request_id}
            return JSONResponse(
                status_code=response.status_code,
                content=payload,
                headers=headers,
            )

        try:
            original = json.loads(body.decode("utf-8"))
        except Exception:
            # If body isn't valid JSON, return original response body unchanged (but headers fixed)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )

        # If already wrapped, keep as-is
        if isinstance(original, dict) and "success" in original:
            return JSONResponse(
                status_code=response.status_code,
                content=original,
                headers=headers,
            )

        payload = {"success": True, "data": original, "request_id": request_id}
        return JSONResponse(
            status_code=response.status_code,
            content=payload,
            headers=headers,
        )