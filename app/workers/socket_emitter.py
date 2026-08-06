from __future__ import annotations

import socketio

from app.core.config import settings


def create_worker_emitter() -> socketio.AsyncServer:
    """A write-only Socket.IO server for emitting from a worker process.

    Backed by the same Redis manager the API uses, so events published here reach
    clients connected to the API via Redis pub/sub. Requires
    ``SOCKETIO_QUEUE_BACKEND=redis`` on both processes; without a shared Redis
    manager a separate process cannot reach connected clients.
    """
    manager = socketio.AsyncRedisManager(settings.socketio_redis_url, write_only=True)
    return socketio.AsyncServer(async_mode="asgi", client_manager=manager)
