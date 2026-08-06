# Messages API

The Messages API separates container-scoped message collection operations from item-level message operations.

## Router Architecture

- **Item operations router** (prefix `/messages`, tags `["messages"]`): Container-agnostic flat routes operating on individual message items by `message_id`. Access is governed by the `MESSAGE_READ` authorization gate on the container.
- **Collection operations router** (prefix `/conversations`, tags `["messages"]`): Collection routes nested under `/conversations/{conversation_id}/messages` for history and creation.

## Item Operations (`/messages/{message_id}`)

- `GET /messages/{message_id}`: Fetch a message by ID.
- `PATCH /messages/{message_id}`: Edit a text message.
- `DELETE /messages/{message_id}`: Delete a message (hard-delete if sender, soft-delete for actor if recipient).
- `GET /messages/{message_id}/thread`: List replies in a thread.
- `GET /messages/{message_id}/thread-summary`: Get thread summary (reply count, last reply timestamp).
- `POST /messages/{message_id}/delivered`: Mark message as delivered.
- `POST /messages/{message_id}/read`: Mark message as read (supports all container types).
- `POST /messages/{message_id}/reactions`: Add reaction emoji.
- `DELETE /messages/{message_id}/reactions/{emoji}/me`: Remove own reaction emoji.
- `POST /messages/{message_id}/forward`: Forward a message to a target conversation.
- `POST /messages/{message_id}/pin`: Pin message (conversation containers only; channel containers return 400 `INVALID_CONTAINER`).
- `DELETE /messages/{message_id}/pin`: Unpin message (conversation containers only).

## Collection Operations (`/conversations/{conversation_id}/messages`)

- `GET /conversations/{conversation_id}/messages`: Paginated message history.
- `POST /conversations/{conversation_id}/messages/text`: Send text message.
- `POST /conversations/{conversation_id}/messages/media`: Multipart media/file upload.
- `POST /conversations/{conversation_id}/messages/content`: Send rich content payload (stickers, locations, contacts, link previews).
- `DELETE /conversations/{conversation_id}/messages`: Clear chat history for user.
- `DELETE /conversations/{conversation_id}/messages/all`: Clear chat history for everyone (admin/owner).
- `POST /conversations/{conversation_id}/messages/schedule`: Schedule a message for future release.
- `GET /conversations/{conversation_id}/messages/scheduled`: List scheduled messages.
- `DELETE /conversations/{conversation_id}/messages/scheduled/{message_id}`: Cancel scheduled message.
- `GET /conversations/{conversation_id}/messages/pinned`: List pinned messages in conversation.

## Validation & Rules

- Text is trimmed and capped at 4000 characters.
- Status transitions are monotonic: `sent -> delivered -> read`.
- Hard delete by sender purges message and owned media storage attachments.
