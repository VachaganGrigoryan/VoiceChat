# Messages

Base path: `/messages`

## Purpose

Messages belong to a container: a `conversation` (DM or group) or a `channel`. See [Architecture](./architecture.md#everything-is-a-container). Two families of routes share the `/messages` prefix:

- **Collection routes** are addressed by container: `/messages/{container_type}/{container_id}/…`.
- **Item routes** are addressed by message id alone: `/messages/{message_id}/…`. The message names its own container, so no container segment is needed.

Access is decided per container by [Authorization](./authorization.md): `message.read`, `message.create`, `thread.reply`, and so on.

## Collection routes

- `GET /messages/{container_type}/{container_id}`
  Paginated history. Query: `limit`, `cursor`.
- `POST /messages/{container_type}/{container_id}/text`
  Send text. Body: `text`, optional `style`, `reply_to_message_id`, `reply_mode`.
- `POST /messages/{container_type}/{container_id}/media`
  Multipart upload. Fields: `file`, `type` (`media` or `file`), `media_kind` (`voice`, `audio`, `image` or `video`), `duration_ms`, `text`, reply fields.
- `POST /messages/{container_type}/{container_id}/content`
  Rich content. `type` is `sticker`, `voice`, `location`, `contact` or `link_preview`, with the matching payload field.
- `POST /messages/{container_type}/{container_id}/schedule`
  Schedule a text message. Body: `text`, `scheduled_for`. The worker releases it when it is due.
- `GET /messages/{container_type}/{container_id}/scheduled`
  The caller's pending scheduled messages.
- `GET /messages/{container_type}/{container_id}/pinned`
  The container's pinned messages.
- `DELETE /messages/{container_type}/{container_id}`
  Clear the caller's **own** view of the container. The caller's messages are removed, and everyone else's are hidden for the caller only.
- `DELETE /messages/{container_type}/{container_id}/all`
  Clear the history for everyone. Requires manage rights.

## Item routes

- `GET /messages/{message_id}`
- `PATCH /messages/{message_id}`
  Edit text. Body: `text`.
- `DELETE /messages/{message_id}`
  Delete. The sender hard-deletes, including stored media. Anyone else hides the message for themselves only.
- `GET /messages/{message_id}/thread`
  Replies in the thread rooted at this message.
- `GET /messages/{message_id}/thread-summary`
  Reply count and last activity.
- `POST /messages/{message_id}/delivered`, `POST /messages/{message_id}/read`
  Receipts.
- `POST /messages/{message_id}/reactions`
  Body: `emoji`.
- `DELETE /messages/{message_id}/reactions/{emoji}/me`
  Remove the caller's reaction.
- `POST /messages/{message_id}/forward`
  Body: `target_conversation_id`.
- `POST /messages/{message_id}/pin`, `DELETE /messages/{message_id}/pin`
  Pin or unpin. Works in both container types.
- `DELETE /messages/{message_id}/scheduled`
  Cancel a scheduled message. Only its author can, and anyone else gets `404`.

## Replies and threads

`reply_to_message_id` with `reply_mode`:

- `quote`: an inline reply in the main timeline
- `thread`: a reply inside the thread rooted at `reply_to_message_id`

In a channel's feed lens, root messages are posts and thread replies are comments.

## Socket Events

- `receive_message`
- `message_status`
- `message_edited`
- `message_deleted`
- `message_reacted`
- `thread_reply_created`, `thread_summary_updated`
- `conversation_history_cleared`, `conversation_pins_updated`
- `notification_created`

Conversations deliver to each participant's `user:{id}` room. Channels deliver once to the `channel:{id}` room. See [Realtime](./realtime.md).

## Notes

- Text is trimmed and capped at 4,000 characters.
- Receipt status only moves forward: `sent`, then `delivered`, then `read`.
- Sending in a DM requires an active connection and no block between the two users.
- `GET /search/messages` searches across containers. See [Search](./search.md).
