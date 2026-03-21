# Messages

Base path: `/messages`

## Endpoints

- `POST /messages/media`
  Multipart upload for `voice`, `image`, `sticker`, and `video`.
  Fields: `type`, `receiver_id`, `file`, optional `text`, optional `duration_ms`.
- `POST /messages/text`
  Send a text message.
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

## Delivery State Rules

- Status transitions are monotonic: `sent -> delivered -> read`.
- Reading a message guarantees `delivered_at`.
- A later delivered call does not downgrade a message that is already read.

## Delete Rules

- If the sender deletes their own message, the message is hard-deleted for everyone.
- Owned media is deleted from storage when the sender hard-deletes the message.
- If the peer deletes a message they received, the message is hidden only for that user.
- Hidden messages are excluded from that user’s history and conversation list.

## Current Sticker Behavior

- `sticker` currently uses the same upload path as other media messages.
- A centralized reusable sticker catalog is not implemented yet.
