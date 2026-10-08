# Realtime

HTTP base path: `/realtime`

## Purpose

Socket.IO carries live updates: new messages, receipts, typing, presence, relationship changes and call signalling. **REST is the source of truth for every write.** Socket events tell other clients that something changed. Socket.IO is served by the same ASGI app as the REST API.

## Connecting

Authenticate with the access token, either in the Socket.IO `auth` payload (`{ "token": "<access_token>" }`) or as a `token` query parameter. On connect the socket joins `user:{user_id}`, and the user's presence becomes `online`.

## Rooms

- `user:{user_id}`
  Joined automatically. Conversation events are sent here, one room per participant.
- `channel:{channel_id}`
  Joined on request with `join_channel`, after a `message.read` check. Channel events are broadcast here once.
- Call rooms
  Joined through `call.join`. See [Calls](./calls.md).

## Presence Endpoints

- `GET /realtime/online-users`
  All currently online user ids.
- `GET /realtime/presence`
  Presence for the given `user_ids`.

## Client Events

- `ping`
  Health check. The server answers `{ "pong": true }`.
- `presence_state`
  Set the caller's state. Body: `{ "state": "online" | "away" | "dnd" }`.
- `join_channel`, `leave_channel`
  Subscribe to or leave a channel room. Body: `{ "channel_id" }`.
- `typing_start`, `typing_stop`
  Body: `{ "container_type", "container_id" }`. In a channel the sender must be allowed to post or reply.
- `message_delivered`, `message_read`
  Body: `{ "message_id" }`.
- `conversation_read`
  Mark a conversation read from the socket. Body: `{ "conversation_id" }`.
- `send_message`
  Compatibility acknowledgement only. It does not persist anything; send through REST.
- `call.*`
  Call signalling. See [Calls](./calls.md).

## Server Events

Messages and threads:

- `receive_message`, `message_status`, `message_edited`, `message_deleted`, `message_reacted`
- `thread_reply_created`, `thread_summary_updated`
- `conversation_history_cleared`, `conversation_pins_updated`
- `conversation_read_ack`, `channel_read`
- `message_ack`, `send_message_ack`
- `typing_start`, `typing_stop`
- `poll_updated`

People and presence:

- `presence_update`
  Carries `user_id`, `state`, `online` and `last_seen_at`.
- `relationship.requested`, `relationship.activated`, `relationship.revoked`
- `block.created`, `block.removed`

Resources and access:

- `space:invite`
- `resource.deleted`
  Payload `{ "resource": { "type", "id" } }`. Clients drop the resource and navigate away from it.
- `capabilities.invalidated`
  Same payload shape. Clients refetch capabilities for the resource.
- `notification_created`

Calls:

- `call.incoming`, `call.accepted`, `call.rejected`, `call.offer`, `call.answer`, `call.ice_candidate`, `call.participant_updated`, `call.connected`, `call.reconnecting`, `call.recovery_available`, `call.resumed`, `call.ended`

Errors:

- `error`
  Body: `{ "code", "message" }`, for example `INVALID_PAYLOAD` or `FORBIDDEN`.

## Notes

- With more than one API process, or with the worker emitting events, set `SOCKETIO_QUEUE_BACKEND=redis` so emits reach every process.
- Presence is stored by `PRESENCE_BACKEND` (`memory` or `redis`).
- Typing and receipts follow the same permission rules as REST messaging.
