from __future__ import annotations

from socketio import ASGIApp

from app.factory import create_app
from app.modules.realtime import register_socket_events, sio

register_socket_events()

app = create_app()
# ✅ The real ASGI app (FastAPI + Socket.IO)
asgi_app = ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")