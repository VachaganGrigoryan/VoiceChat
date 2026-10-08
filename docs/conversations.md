# Conversations

Base path: `/conversations`

## Purpose

A conversation is a message container of type `conversation`. It is either a **DM** between two people or a **group**. A group may be standalone or owned by a space. Sending and reading messages is covered in [Messages](./messages.md). This page covers the conversation resource itself, its members and the caller's inbox state.

## Inbox

- `GET /conversations`
  The caller's inbox. Query: `limit`, `cursor`, `archived`, `folder`, `space_id`.
- `POST /conversations`
  Create or return the DM with a peer. Body: `peer_user_id`. Requires an active connection.
- `GET /conversations/{conversation_id}`
  One conversation with the caller's state.
- `DELETE /conversations/{conversation_id}`
  **Clears the caller's message history.** It does not delete the conversation.
- `POST /conversations/{conversation_id}/read`
  Mark the conversation read.
- `PATCH /conversations/{conversation_id}/inbox`
  Set `pinned`, `archived` or `folder` for the caller.
- `PATCH /conversations/inbox`
  The same, in bulk. Body: `conversation_ids` plus the fields to set.
- `GET /conversations/folders`
  The caller's folders.
- `PATCH /conversations/folders/{name}`
  Rename a folder. Body: `new_name`.
- `DELETE /conversations/folders/{name}`
  Delete a folder.
- `GET /conversations/{conversation_id}/draft`, `PUT /conversations/{conversation_id}/draft`, `DELETE /conversations/{conversation_id}/draft`
  The caller's unsent draft, stored on the server.

## Groups

- `POST /conversations/groups`
  Create a group. Body: `title`, `participant_ids`, optional `space_id` and `space_visibility`.
- `PATCH /conversations/groups/{conversation_id}`
  Rename the group. Body: `title`.
- `DELETE /conversations/groups/{conversation_id}`
  **Delete the group.**
- `PATCH /conversations/groups/{conversation_id}/avatar`
  Upload a group avatar (multipart `file`).
- `DELETE /conversations/groups/{conversation_id}/avatar`
  Remove the group avatar.
- `PATCH /conversations/{conversation_id}/settings`
  Group settings. Body: `allow_member_polls`.
- `GET /conversations/public/{slug}`
  Preview a public group by slug.

## Members

- `GET /conversations/{conversation_id}/members`
- `POST /conversations/{conversation_id}/members`
  Add members. Body: `participant_ids`.
- `DELETE /conversations/{conversation_id}/members/{member_user_id}`
  Remove a member.
- `PATCH /conversations/{conversation_id}/members/{member_user_id}/role`
  Change a member's role. Body: `role`.
- `PATCH /conversations/{conversation_id}/members/{member_user_id}/permissions`
  Set per-member permission overrides. Body: `permissions`.
- `POST /conversations/{conversation_id}/ownership`
  Transfer ownership. Body: `user_id`.
- `POST /conversations/{conversation_id}/leave`
  Leave a group.

## Invites and join requests

- `GET /conversations/{conversation_id}/invites`
- `POST /conversations/{conversation_id}/invites`
  Create an invite code. Body: `expires_at`, `max_uses`, `approval_required`, `role_ids`.
- `DELETE /conversations/{conversation_id}/invites/{invite_id}`
  Revoke an invite.
- `POST /conversations/invites/{code}/redeem`
  Redeem an invite code. With `approval_required`, this files a join request instead of joining.
- `GET /conversations/{conversation_id}/join-requests`
- `POST /conversations/{conversation_id}/join-requests/{request_id}/approve`
- `POST /conversations/{conversation_id}/join-requests/{request_id}/reject`

Generic join, invite and accept routes are in [Relationships](./relationships.md#memberships). Roles and capabilities are in [Authorization](./authorization.md).

## Socket Events

- `receive_message`, `message_status`, `message_edited`, `message_deleted`, `message_reacted`
- `conversation_history_cleared`
- `conversation_pins_updated`
- `typing_start`, `typing_stop`

See [Realtime](./realtime.md).

## Notes

- Membership is stored in `relationships`. `conversation_participants` is a compatibility view, not a collection to query.
- Deleting a space-owned group goes through the cascade in `app/modules/resources/cascade.py`.
