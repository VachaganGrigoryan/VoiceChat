from __future__ import annotations

import socketio

from app.core.config import settings


def create_client_manager():
    if settings.socketio_queue_backend == "redis":
        return socketio.AsyncRedisManager(settings.socketio_redis_url)
    return None


def create_socket_server() -> socketio.AsyncServer:
    manager = create_client_manager()

    return socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
        client_manager=manager,
    )


def register_socket_events(sio: socketio.AsyncServer) -> None:
    # Import here to avoid circular imports
    from app.modules.realtime.events import register_events

    register_events(sio)