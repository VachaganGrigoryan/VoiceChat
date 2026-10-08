# Feeds

Base path: `/feeds`

## Purpose

Feeds are read models over channel messages. A **post** is a root message in a channel, and its **comments** are the thread replies. Posts are written through [Messages](./messages.md), or `POST /users/me/posts` for the profile channel. There is no separate post collection.

## Endpoints

- `GET /feeds`
  The home feed: newest posts from the people and channels the caller follows. Query: `limit`, `cursor`.
- `GET /feeds/channels/{channel_id}`
  One channel's posts. Query: `limit`, `cursor`.
- `GET /feeds/channels/{channel_id}/posts/{post_id}/comments`
  Comments on one post.
- `GET /feeds/users/{username}`
  A user's profile feed, which is their profile channel's posts. Query: `limit`, `cursor`.

## Notes

- Follows drive the home feed. See [Relationships](./relationships.md#follows).
- A channel's `comment_policy` decides who may comment. See [Channels](./channels.md#policies).
- Saved posts are in [Saved messages](./saved.md).
