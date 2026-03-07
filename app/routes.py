from __future__ import annotations

from fastapi import FastAPI

from app.health.router import router as health_router
from app.modules.auth.router import router as auth_router
from app.modules.messages.router import router as messages_router
from app.modules.realtime.router import router as realtime_router


def register_routers(app: FastAPI) -> None:
    # health routes first, intentionally public
    app.include_router(health_router)

    app.include_router(auth_router)
    app.include_router(messages_router)
    app.include_router(realtime_router)