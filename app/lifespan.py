from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.indexes import ensure_indexes
from app.db.mongo import connect_mongo, disconnect_mongo, get_db
from app.infra.queue import get_job_queue
from app.modules.realtime.presence import close_presence_backend


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    os.makedirs(settings.upload_dir, exist_ok=True)

    await connect_mongo()
    await ensure_indexes(get_db())

    yield

    # Shoutdown
    await disconnect_mongo()
    await get_job_queue().close()
    await close_presence_backend()
