from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.core.http import RequestIdMiddleware, SuccessEnvelopeMiddleware
from app.lifespan import lifespan
from app.routes import register_routers


def create_app() -> FastAPI:
    app = FastAPI(
        title="Real-Time Voice Chat API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,  # List of allowed origins
        allow_credentials=True,  # Allow cookies and authorization headers
        allow_methods=["*"],  # Allow all methods (GET, POST, PUT, DELETE, etc.)
        allow_headers=["*"],  # Allow all headers
    )

    register_middlewares(app)
    register_exception_handlers(app)
    register_routers(app)

    # Serve local uploads (dev/local storage mode)
    # This gives URLs like: /media/<filename>
    mount_local_storage(app)

    return app


def register_middlewares(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SuccessEnvelopeMiddleware)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


def mount_local_storage(app: FastAPI) -> None:
    if settings.storage_provider != "local":
        return

    os.makedirs(settings.upload_dir, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.upload_dir), name="media")