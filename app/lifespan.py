from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.indexes import ensure_indexes
from app.db.mongo import connect_mongo, disconnect_mongo, get_db
from app.infra.queue import get_job_queue
from app.modules.calls.repository import CallsRepository
from app.modules.calls.ws import (
    close_call_runtime,
    schedule_call_expiration,
    schedule_call_reconnect_timeout,
)
from app.modules.realtime.presence import close_presence_backend


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    os.makedirs(settings.upload_dir, exist_ok=True)

    await connect_mongo()
    await ensure_indexes(get_db())
    calls_repo = CallsRepository(get_db())
    await calls_repo.expire_stale_calls()

    sio = getattr(app.state, "sio", None)
    if sio is not None:
        live_calls = await calls_repo.find_live_calls(statuses=("ringing", "reconnecting"))
        for call_doc in live_calls:
            call_id = str(call_doc["_id"])
            if call_doc.get("status") == "ringing":
                schedule_call_expiration(
                    sio,
                    call_id=call_id,
                    expires_at=call_doc.get("expires_at"),
                )
            if call_doc.get("status") == "reconnecting":
                schedule_call_reconnect_timeout(
                    sio,
                    call_id=call_id,
                    reconnect_deadline_at=call_doc.get("reconnect_deadline_at"),
                )

    yield

    # Shoutdown
    await disconnect_mongo()
    await get_job_queue().close()
    await close_call_runtime()
    await close_presence_backend()
