from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.indexes import ensure_indexes
from app.db.mongo import connect_mongo, disconnect_mongo, get_db
from app.infra.queue import get_job_queue
from app.modules.auth.refresh_repository import RefreshTokensRepository
from app.modules.auth.repository import UsersRepository
from app.modules.realtime.presence import close_presence_backend

from app.modules.passkeys.service import PasskeyService
from app.modules.passkeys.repository import (
    PasskeysRepository,
    PasskeyChallengesRepository,
)
from app.modules.auth.service import AuthService
from app.modules.verification.repository import VerificationCodesRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    os.makedirs(settings.upload_dir, exist_ok=True)

    await connect_mongo()
    await ensure_indexes(get_db())

    db = get_db()
    users_repo = UsersRepository(db)
    codes_repo = VerificationCodesRepository(db)
    refresh_repo = RefreshTokensRepository(db)

    auth_service = AuthService(
        users=users_repo,
        codes=codes_repo,
        refresh_tokens=refresh_repo,
    )

    app.state.passkey_service = PasskeyService(
        passkeys_repo=PasskeysRepository(db),
        challenges_repo=PasskeyChallengesRepository(db),
        users_repo=users_repo,
        auth_service=auth_service,
    )

    yield

    # Shoutdown
    await disconnect_mongo()
    await get_job_queue().close()
    await close_presence_backend()
