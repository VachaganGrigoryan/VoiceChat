from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, ANY

import pytest

from app.modules.realtime.emits import emit_presence_update


@pytest.mark.asyncio
async def test_emit_presence_update_includes_state_and_last_seen():
    sio = AsyncMock()
    last_seen_at = datetime(2026, 7, 22, 10, 30, tzinfo=UTC)

    await emit_presence_update(
        sio,
        "u1",
        "offline",
        last_seen_at=last_seen_at,
        skip_sid="sid1",
    )

    sio.emit.assert_awaited_once_with(
        "presence_update",
        {
            "user_id": "u1",
            "state": "offline",
            "status": "offline",
            "online": False,
            "last_seen_at": ANY,
        },
        skip_sid="sid1",
    )
