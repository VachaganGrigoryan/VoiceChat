from __future__ import annotations

from fastapi import FastAPI

from app.health.router import router as health_router
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.conversations.router import router as conversations_router
from app.modules.devices.router import router as devices_router
from app.modules.realtime.router import router as realtime_router
from app.modules.passkeys.router import router as passkeys_router
from app.modules.pings.router import router as pings_router
from app.modules.discovery.router import router as discovery_router
from app.modules.calls.router import router as calls_router
from app.modules.webrtc.router import router as webrtc_router
from app.modules.notifications.router import router as notifications_router
from app.modules.saved.router import router as saved_router
from app.modules.search.router import router as search_router
from app.bots.poll.router import router as polls_router
from app.modules.spaces import spaces_router
from app.modules.extensibility.router import extensibility_router


def register_routers(app: FastAPI) -> None:
    # health routes first, intentionally public
    app.include_router(health_router)

    app.include_router(auth_router)
    app.include_router(passkeys_router)
    app.include_router(users_router)
    app.include_router(pings_router)
    app.include_router(discovery_router)
    app.include_router(conversations_router)
    app.include_router(devices_router)
    app.include_router(calls_router)
    app.include_router(webrtc_router)
    app.include_router(notifications_router)
    app.include_router(saved_router)
    app.include_router(search_router)
    app.include_router(polls_router)
    app.include_router(spaces_router)
    app.include_router(extensibility_router)
    app.include_router(realtime_router)

