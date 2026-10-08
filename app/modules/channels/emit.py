from __future__ import annotations

import socketio

from app.modules.messages.emit_helpers import fan_out_container_send
from app.modules.messages.service import SendMessageResult
from app.modules.notifications.service import NotificationsService


async def fan_out_channel_message(
    sio: socketio.AsyncServer,
    *,
    result: SendMessageResult,
    notifications: NotificationsService,
) -> None:
    """Deliver a freshly created channel message over realtime.

    Broadcasts once to the channel's Socket.IO room -- no membership/follower
    lookup, no fan-out ceiling. The channel id is read off the message
    envelope so every producer of a channel message -- the channel routes and
    profile posts alike -- can share one fan-out path.
    """
    await fan_out_container_send(
        sio,
        container_type="channel",
        container_id=result.message.container_id,
        result=result,
        notifications=notifications,
    )
