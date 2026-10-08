# Channels

Base path: `/channels`

## Purpose

A channel is a message container of type `channel`. It can be standalone or owned by a space. A channel has two presentations in the client, called lenses:

- **Feed**: root messages rendered as posts, with thread replies as comments. Served by [Feeds](./feeds.md).
- **Chat**: the same messages as a timeline. Served by [Messages](./messages.md).

Every user also owns a profile channel, which backs their profile feed.

## Endpoints

- `POST /channels`
  Create a standalone channel. Body: `name`, `slug`, `kind` (`text` or `announcement`), `visibility` (`public`, `members` or `private`), `join_policy`, `posting_policy`, `comment_policy`, optional `description` and `tags`.
- `GET /channels/me`
  The caller's channels with per-user state, for the chat inbox.
- `GET /channels/{channel_id}`
- `PATCH /channels/{channel_id}`
  Update `name`, `description`, `avatar`, `banner`, `tags`, `visibility`, `join_policy`, `posting_policy` and `comment_policy`.
- `DELETE /channels/{channel_id}`
  Delete the channel. Gated by `resource.delete` on the channel.
- `GET /channels/{channel_id}/members`
- `POST /channels/{channel_id}/leave`
- `POST /channels/{channel_id}/read`
  Mark the channel read.
- `PATCH /channels/{channel_id}/inbox`
  Set `pinned`, `archived` or `folder` for the caller.
- `PATCH /channels/{channel_id}/notifications`
  Body: `notification_level` (`all`, `mentions` or `none`), `muted_until`.

Follow, join and invite routes are in [Relationships](./relationships.md). Roles and capabilities are in [Authorization](./authorization.md).

## Policies

- `posting_policy`: who may publish root posts (`owner`, `moderators`, `members` or `everyone`).
- `comment_policy`: who may reply in threads (`disabled`, `followers`, `members` or `everyone`).
- `join_policy`: `open`, `approval`, `invite_only` or `closed`. Only `open` channels are self-joinable.

Policies are enforced inside `AuthorizationService.can()`. They cap what roles can grant, and they can grant on their own, for example open commenting.

## Socket Events

Channel events fan out to the `channel:{channel_id}` room. A client subscribes with the `join_channel` socket event, and only after a read check.

- `receive_message`, `message_edited`, `message_deleted`, `message_reacted`
- `thread_reply_created`, `thread_summary_updated`
- `typing_start`, `typing_stop`
- `resource.deleted`

`channel_read` is sent to the reader's own `user:{id}` room, not to the channel room, so the reader's other devices can clear their unread badge.

## Notes

- `kind: announcement` is meant for owner-posted updates with open comments. `text` is a discussion channel.
- Channel fan-out is one broadcast to the room. There is no per-member loop and no fan-out ceiling.
