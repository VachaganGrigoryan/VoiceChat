from __future__ import annotations

from fastapi import APIRouter, Response, status
from starlette.requests import Request

from app.core.api_models import SuccessResponse
from app.core.responses import ok
from app.health.schemas import HealthStatus, LiveStatus
from app.health.service import HealthService

router = APIRouter(tags=["health"], include_in_schema=True)


@router.get(
    "/health/live",
    response_model=SuccessResponse[LiveStatus],
)
async def live(request: Request):
    return ok(request, data={"status": "up"})


@router.get(
    "/health/ready",
    response_model=SuccessResponse[HealthStatus],
)
async def ready(request: Request, response: Response):
    """
    Readiness probe.
    Returns 200 only when required dependencies are healthy.
    """
    service = HealthService()
    result = await service.readiness()

    if result["status"] != "up":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ok(request, data=result)
