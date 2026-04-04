# Voice Chat Backend — External API / WebSocket Updates Recap

## REST API

### Auth
- `POST /auth/start`
- `POST /auth/finish`
- `POST /auth/refresh`
- `POST /auth/logout`

`/auth/start` accepts a method-based auth request. Only `email` is supported in this version:

- `method`
- `identifier`

`/auth/finish` completes the same flow:

- `method`
- `identifier`
- `code`

---

### Users

#### GET `/users/me`
Returns current user profile.

**Response data fields**
- `id`
- `email`
- `is_verified`
- `username`
- `display_name`
- `bio`
- `avatar`
- `is_private`
- `default_discovery_enabled`
- `last_seen_at`
- `username_updated_at`
- `created_at`
- `updated_at`

**avatar**
- `storage`
- `key`
- `url`
- `mime`
- `size_bytes`

---

#### PATCH `/users/me`
Updates editable profile fields.

**Input**
- `display_name`
- `bio`
- `is_private`
- `default_discovery_enabled`

**Response**
- updated user profile object

---

#### PATCH `/users/me/username`
Updates username.

**Input**
- `username`

**Response**
- updated user profile object

---

#### PATCH `/users/me/avatar`
Uploads avatar as multipart file.

**Input**
- multipart form field: `file`

**Rules**
- allowed mime:
  - `image/jpeg`
  - `image/png`
  - `image/webp`
- max size:
  - `5 MB`

**Response**
- updated user profile object

---

#### DELETE `/users/me/avatar`
Deletes current avatar.

**Response**
- updated user profile object

---

### Messages

#### POST `/messages/media`
Creates file-bearing message.

**Input**
- `receiver_id`
- `type` as `media` or `file`
- `file`
- optional `media_kind` when `type=media`
- optional `text`
- optional `duration_ms`

**Response data fields**
- `id`
- `conversation_id`
- `sender_id`
- `receiver_id`
- `type`
- `text`
- `media`
- `status`
- `edited_at`
- `delivered_at`
- `read_at`
- `created_at`
- `updated_at`

---

#### POST `/messages/text`
Creates text message.

**Input**
- `receiver_id`
- `text`

**Response**
- created message object

---

#### GET `/messages/conversations/{user_id}`
Gets message history with a specific user.

**Query params**
- `cursor`
- `limit`

**Response**
- paginated list of message objects

**Pagination meta**
- `cursor`
- `next_cursor`
- `limit`
- `total`

---

### Conversations

#### GET `/conversations`
Gets recent conversations for current user.

**Query params**
- `cursor`
- `limit`

**Response data item fields**
- `conversation_id`
- `peer_user`
- `last_message`
- `last_message_at`

**peer_user**
- `id`
- `username`
- `display_name`
- `avatar`
- `is_online`

**last_message**
- `id`
- `type`
- `text`
- `media`
- `status`
- `created_at`

**Pagination meta**
- `cursor`
- `next_cursor`
- `limit`
- `total`

---

## Message Object

### Common fields
- `id`
- `conversation_id`
- `sender_id`
- `receiver_id`
- `type`
- `text`
- `media`
- `status`
- `edited_at`
- `delivered_at`
- `read_at`
- `created_at`
- `updated_at`

### `type`
Supported:
- `text`
- `media`
- `file`

### `media`
Used for file-bearing payloads.

**Fields**
- `kind`
- `storage`
- `key`
- `url`
- `mime`
- `size_bytes`
- `duration_ms`

### `media.kind`
- `voice`
- `audio`
- `image`
- `video`
- `file`

---

## WebSocket Events

## Client → Server

### `send_message`
Generic send acknowledgement event.

**Input**
- `to`
- `message_id`
- `type` optional

**Ack**
- `send_message_ack`

**Ack fields**
- `message_id`
- `to`
- `type`
- `accepted`

---

### `message_delivered`
Marks message as delivered.

**Input**
- `message_id`

**Ack**
- `message_ack`

**Ack fields**
- `message_id`
- `status`

---

### `message_read`
Marks message as read.

**Input**
- `message_id`

**Ack**
- `message_ack`

**Ack fields**
- `message_id`
- `status`

---

### `typing_start`
**Input**
- `to`

### `typing_stop`
**Input**
- `to`

---

## Server → Client

### `receive_message`
Generic incoming message event.

**Payload**
- message object

---

### `message_status`
Generic message status update.

**Payload**
- `message_id`
- `status`
- `type`
- `delivered_at` optional
- `read_at` optional

---

### `message_ack`
Acknowledgement for delivered/read actions.

**Payload**
- `message_id`
- `status`

---

### `presence_update`
**Payload**
- existing presence payload shape

---

### `typing_start`
**Payload**
- `from`

### `typing_stop`
**Payload**
- `from`

---

## Deprecated / Replaced WebSocket Names

Replaced by generic events:
- `send_voice_message` → `send_message`
- `voice_message_delivered` → `message_delivered`
- `voice_message_read` → `message_read`
- `receive_voice_message` → `receive_message`
- `voice_message_status` → `message_status`
