from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.middleware import RequestIdMiddleware, SuccessEnvelopeMiddleware
from app.core.error_handlers import (
    app_error_handler,
    validation_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
)
from app.core.exceptions import AppError
from app.core.config import settings
from app.core.logging import setup_logging

from app.db.mongo import connect_mongo, disconnect_mongo, get_db
from app.db.indexes import ensure_indexes

from app.modules.auth.router import router as auth_router


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

# Serve local uploads (dev/local storage mode)
# This gives URLs like: /media/<filename>
app.mount("/media", StaticFiles(directory=settings.upload_dir), name="media")


@app.get("/health")
async def health():
    # Quick DB ping (optional but useful)
    db = get_db()
    await db.command("ping")
    return {"status": "ok", "env": settings.app_env}


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_, exc: RuntimeError):
    return JSONResponse(status_code=500, content={"error": {"message": str(exc)}})
