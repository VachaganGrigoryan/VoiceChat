# Users

Base path: `/users`

## Endpoints

- `GET /users/me`
  The current user's profile.
- `PATCH /users/me`
  Update `display_name`, `bio`, `pronouns`, `timezone`, `is_private` and `default_discovery_enabled`.
- `PATCH /users/me/username`
  Set or change the username. New accounts must call this after sign-up.
- `PATCH /users/me/avatar`
  Upload an avatar (multipart `file`).
- `DELETE /users/me/avatar`
  Remove the avatar and its stored file.
- `PATCH /users/me/status`
  Set a status. Body: `status_emoji`, `status_text`, `status_expires_at`.
- `DELETE /users/me/status`
  Clear the status.
- `PATCH /users/me/main-channel`
  Choose which of the user's channels is their main, profile-facing channel. Body: `channel_id`.
- `POST /users/me/posts`
  Publish a post to the user's own profile channel. Same body as a text message.
- `GET /users/{id}`
  A user's public profile. `include` adds extra sections, for example `contact_details`.
- `GET /users/{user_id}/channels`
  A user's channels, including their profile channel. Paginated with `limit` and `offset`.

Follow endpoints under `/users/{user_id}/…` are documented in [Relationships](./relationships.md). `GET /users/{user_id}/prekey-bundle` is in [Devices](./devices.md).

## Notes

- Avatars go through the configured storage backend (`STORAGE_PROVIDER`).
- `is_private: true` turns follows into requests that the user must accept.
- Profile posts are channel messages, so they appear in the user's profile feed (`GET /feeds/users/{username}`) and in their followers' home feeds. See [Feeds](./feeds.md).
