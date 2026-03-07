from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from socketio import ASGIApp

from app.core.config import settings
from app.core.middleware import RequestIdMiddleware, SuccessEnvelopeMiddleware
from app.core.exceptions import AppError
from app.core.error_handlers import (
    app_error_handler,
    validation_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
)
from app.core.logging import setup_logging
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db.mongo import connect_mongo, disconnect_mongo, get_db
from app.db.indexes import ensure_indexes
from app.infra.queue import get_job_queue

from app.modules.auth.router import router as auth_router
from app.modules.messages.router import router as messages_router
from app.modules.realtime.presence import close_presence_backend
from app.modules.realtime.router import router as realtime_router
from app.modules.realtime import register_socket_events, sio

# register handlers before ASGI wrapper
register_socket_events()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    # Ensure upload dir exists (for local storage mode)
    os.makedirs(settings.upload_dir, exist_ok=True)

    # Mongo connect + indexes
    await connect_mongo()
    await ensure_indexes(get_db())

    yield

    # Shutdown
    await disconnect_mongo()

    # Close job queue
    await get_job_queue().close()

    # Close presence backend
    await close_presence_backend()


app = FastAPI(
    title="Real-Time Voice Chat API",
    version="0.1.0",
    lifespan=lifespan,
)

# middleware
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SuccessEnvelopeMiddleware)

# exception handlers
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# routers
app.include_router(auth_router)
app.include_router(messages_router)
app.include_router(realtime_router)

# Serve local uploads (dev/local storage mode)
# This gives URLs like: /media/<filename>
app.mount("/media", StaticFiles(directory=settings.upload_dir), name="media")

# ✅ The real ASGI app (FastAPI + Socket.IO)
asgi_app = ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io",)

@app.get("/health")
async def health():
    # Quick DB ping (optional but useful)
    db = get_db()
    await db.command("ping")
    return {"status": "ok", "env": settings.app_env}


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_, exc: RuntimeError):
    return JSONResponse(status_code=500, content={"error": {"message": str(exc)}})
