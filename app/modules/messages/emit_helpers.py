from __future__ import annotations

import socketio

from app.modules.messages.service.base import SendMessageResult
from app.modules.realtime import emit_to_user


async def emit_send_result(
    sio: socketio.AsyncServer,
    *,
    result: SendMessageResult,
    participant_ids: list[str] | None = None,
) -> None:
    """Fan out a freshly-created message over realtime.

    Thread replies use `thread_reply_created` + `thread_summary_updated`; all other
    messages use `receive_message`. Event names stay snake_case and every payload
    carries the `{ container_type, container_id }` envelope, so a channel root
    (`container_type = channel`, `thread_root_id = null`) is the Post event and
    needs no `post.created` of its own (§77–78).
    """
    payload = result.message.model_dump(mode="json")

    if result.thread_summary is not None:
        if participant_ids is not None:
            for participant_id in participant_ids:
                await emit_to_user(sio, participant_id, "thread_reply_created", payload)
                await emit_to_user(
                    sio,
                    participant_id,
                    "thread_summary_updated",
                    {
                        "thread_root_id": result.thread_summary.thread_root_id,
                        "container_type": result.thread_summary.container_type,
                        "container_id": result.thread_summary.container_id,
                        "conversation_id": result.thread_summary.conversation_id,
                        "thread_reply_count": result.thread_summary.thread_reply_count,
                        "last_thread_reply_at": result.thread_summary.last_thread_reply_at,
                    },
                )
            return
        return

    if participant_ids is not None:
        for participant_id in participant_ids:
            if participant_id != result.message.sender_id:
                await emit_to_user(sio, participant_id, "receive_message", payload)
        return
