from __future__ import annotations

from socketio import ASGIApp

from app.factory import create_app
from app.socket import create_socket_server, register_socket_events


sio = create_socket_server()
register_socket_events(sio)

app = create_app()
# Attach to app state
app.state.sio = sio
# ✅ The real ASGI app (FastAPI + Socket.IO)
asgi_app = ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")