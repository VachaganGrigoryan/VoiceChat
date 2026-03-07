from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.error_handlers import (
    app_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.core.exceptions import AppError
from app.core.middleware import RequestIdMiddleware, SuccessEnvelopeMiddleware
from app.lifespan import lifespan
from app.routes import register_routers


def create_app() -> FastAPI:
    app = FastAPI(
        title="Real-Time Voice Chat API",
        version="0.1.0",
        lifespan=lifespan,
    )

    register_middlewares(app)
    register_exception_handlers(app)
    register_routers(app)

    # Serve local uploads (dev/local storage mode)
    # This gives URLs like: /media/<filename>
    app.mount("/media", StaticFiles(directory=settings.upload_dir), name="media")

    return app


def register_middlewares(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SuccessEnvelopeMiddleware)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)