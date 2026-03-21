# Realtime

HTTP base path: `/realtime`

## Presence Endpoints

- `GET /realtime/online-users`
  Return all currently online user ids.
- `GET /realtime/presence`
  Return online status for a provided list of user ids.

## Socket Rooms

- Each authenticated socket joins `user:{user_id}`.

## Client Events

- `ping`
  Health check event.
- `typing_start`
  Requires `to`. Chat permission is enforced.
- `typing_stop`
  Requires `to`. Chat permission is enforced.
- `send_message`
  Compatibility ack event only. REST remains the source of truth for persistence. Chat permission is enforced.
- `message_delivered`
  Requires `message_id`.
- `message_read`
  Requires `message_id`.
- `conversation_read`
  Requires `peer_user_id`.

## Server Events

- `receive_message`
  New incoming message payload.
- `message_status`
  Delivery or read updates.
- `message_edited`
  Message edit event for both participants.
- `message_deleted`
  Hard-delete for everyone or hide-for-me acknowledgement, depending on actor.
- `presence_update`
  Online or offline change.
- `ping_received`
- `ping_accepted`
- `ping_declined`
- `ping_cancelled`
- `chat_permission_updated`
- `user_blocked`

## Notes

- Typing and compatibility send events now follow the same permission rules as REST messaging.
- Message persistence still happens through REST endpoints.
