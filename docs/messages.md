# Messages

Base path: `/messages`

## Endpoints

- `POST /messages/media`
  Multipart upload for `voice`, `image`, and `video`.
  Fields: `type`, `receiver_id`, `file`, optional `text`, optional `duration_ms`.
- `POST /messages/text`
  Send a text message.
- `POST /messages/sticker`
  Send a sticker reference. Input: `receiver_id`, `sticker_id`, optional `emoji`.
- `GET /messages/conversations/{user_id}`
  Paginated history with a peer.
- `GET /messages/conversations`
  Paginated recent conversation list with unread counts and peer summary.
- `POST /messages/{message_id}/delivered`
  Mark a message as delivered.
- `POST /messages/{message_id}/read`
  Mark a message as read.
- `POST /messages/conversations/{user_id}/read`
  Mark all visible messages in a conversation as read for the current receiver.
- `PATCH /messages/{message_id}`
  Edit a text message within the edit window.
- `DELETE /messages/{message_id}`
  Delete behavior depends on who performs the action.

## Validation

- Text is trimmed and capped at 4000 characters.
- Media captions use the same max length and empty captions normalize to `null`.
- `duration_ms` must be greater than or equal to `0`.
- Media mime type and file size are validated by message type.
- Sticker sending validates that the current user owns the sticker and that the pack is published.

## Delivery State Rules

- Status transitions are monotonic: `sent -> delivered -> read`.
- Reading a message guarantees `delivered_at`.
- A later delivered call does not downgrade a message that is already read.

## Delete Rules

- If the sender deletes their own message, the message is hard-deleted for everyone.
- Owned media is deleted from storage when the sender hard-deletes the message.
- If the peer deletes a message they received, the message is hidden only for that user.
- Hidden messages are excluded from that user’s history and conversation list.

## Sticker Behavior

- Sticker uploads and catalog management live under `/stickers`.
- Sticker messages persist a `sticker` reference object and the backend hydrates `media` from the sticker asset record for direct rendering.
- `POST /stickers/resolve` is still available for batch/catalog/preload flows, but normal chat rendering should not require it.
