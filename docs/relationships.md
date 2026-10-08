# Relationships

## Purpose

The `relationships` collection is the single table behind three kinds of relationship. Each kind has its own routes:

- **Connections**: mutual, user to user. An active connection is what allows direct messages and calls. The UI calls a connection request a *ping*.
- **Follows**: one-way, from a user to a user or to a channel. Following a private account creates a request.
- **Memberships**: a user in a conversation, channel or space, with roles and permission overrides.

The same row also carries per-viewer inbox state (pinned, archived, folder, notification level, draft). `ParticipantDocument`, `SpaceMemberDocument` and `JoinRequestDocument` are compatibility views over this table, not collections. See [Architecture](./architecture.md#one-membership-table).

## Connections

Base path: `/connections`

- `POST /connections/{user_id}/ping`
  Create or reopen a connection request.
- `GET /connections/pending`
  Pending requests. Filter with `direction=incoming|outgoing`.
- `GET /connections`
  Active connections with peer and direct-conversation summaries.
- `POST /connections/{relationship_id}/accept`
  Accept an incoming request.
- `POST /connections/{relationship_id}/decline`
  Decline an incoming request.
- `DELETE /connections/{relationship_id}`
  Revoke a pending or active connection.

## Follows

- `POST /users/{user_id}/follow`, `DELETE /users/{user_id}/follow`
  Follow or unfollow a user. A private account turns the follow into a pending request.
- `GET /users/{user_id}/followers`, `GET /users/{user_id}/following`
  Follow lists, limited with `limit`.
- `POST /channels/{channel_id}/follow`, `DELETE /channels/{channel_id}/follow`
  Follow or unfollow a channel. Followed channels feed the home feed.
- `POST /follows/{relationship_id}/accept`, `POST /follows/{relationship_id}/decline`
  Answer a follow request on a private account.

## Memberships

These routes share one implementation across the three joinable resource types.

- `POST /channels/{target_id}/join`
- `POST /conversations/{target_id}/join`
  Join directly when the resource's join policy allows it.
- `POST /channels/{target_id}/invite/{user_id}`
- `POST /conversations/{target_id}/invite/{user_id}`
- `POST /spaces/{target_id}/invite/{user_id}`
  Invite a user. The invitee accepts or declines.
- `POST /channels/{target_id}/members/{relationship_id}/accept`, `POST /channels/{target_id}/members/{relationship_id}/decline`
- `POST /conversations/{target_id}/members/{relationship_id}/accept`, `POST /conversations/{target_id}/members/{relationship_id}/decline`
- `POST /spaces/{target_id}/members/{relationship_id}/accept`, `POST /spaces/{target_id}/members/{relationship_id}/decline`
  Accept or decline a pending membership.

Roles on a membership are replaced with `PUT /relationships/{relationship_id}/roles`. See [Authorization](./authorization.md#roles).

## Socket Events

- `relationship.requested`
- `relationship.activated`
- `relationship.revoked`
- `capabilities.invalidated`
  Sent when a membership change may alter what a user can do.

## Notes

- Collection endpoints use cursor pagination with `limit` and `cursor`.
- Messaging, typing and calls between two users require an active connection.
- Deleting direct-message history does not revoke the connection.
- Blocking revokes the connection, and unblocking does not recreate it. See [Blocks](./blocks.md).
- Joining a space always files a join request, even when the space's join policy is `open`. See [Spaces](./spaces.md).
