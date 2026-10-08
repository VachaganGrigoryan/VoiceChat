# Spaces

Base path: `/spaces`

## Purpose

A space is a workspace or community. It owns channels and groups and has one member list. The space owner is the effective owner of everything the space owns. See [Authorization](./authorization.md#ownership-is-a-bypass-not-a-role).

## Endpoints

- `POST /spaces`
  Create a space. Body: `name`, `slug`, `kind` (`workspace` or `community`), `visibility` (`private` or `public`), `join_policy` (`open`, `approval`, `invite_only` or `closed`), optional `avatar` and `settings`.
- `GET /spaces/me`
  Spaces the caller belongs to.
- `GET /spaces/{space_id}`
- `PATCH /spaces/{space_id}`
  Update `name`, `visibility` or `settings`.
- `DELETE /spaces/{space_id}`
  Delete the space **and every channel and group it owns**. This is the widest destructive action in the system, so it has a tighter rate limit.

### Content

- `GET /spaces/{space_id}/channels`
  Channels in the space that the caller may read.
- `POST /spaces/{space_id}/channels`
  Create a channel in the space. Same fields as `POST /channels`, except that `join_policy` is derived from `visibility`.
- `POST /spaces/{space_id}/channels/{channel_id}/join`
  Join an open channel in the space. The caller must be a space member.
- `GET /spaces/{space_id}/groups`
- `POST /spaces/{space_id}/groups`
  Create a space-owned group. Body: `title`, `participant_ids`.

### Members, invites and join requests

- `GET /spaces/{space_id}/members`
- `POST /spaces/{space_id}/join`
  Request to join. This **always** creates a join request, even under `join_policy: open`.
- `GET /spaces/{space_id}/join-requests`
- `POST /spaces/{space_id}/join-requests/{request_id}/approve`
- `POST /spaces/{space_id}/join-requests/{request_id}/reject`
- `GET /spaces/{space_id}/invites`
- `POST /spaces/{space_id}/invites`
  Create an invite code. Body: `expires_at`, `max_uses`, `approval_required`, `role_ids`.
- `POST /spaces/{space_id}/invites/user`
  Invite one user. Body: `user_id`.
- `DELETE /spaces/{space_id}/invites/{invite_id}`
- `POST /spaces/invites/{code}/redeem`

Generic invite and accept routes are in [Relationships](./relationships.md#memberships). Roles and capabilities are in [Authorization](./authorization.md#roles).

## Socket Events

- `space:invite`
  Sent to the invited user.
- `resource.deleted`
  Sent to the space's audience when the space, or one of its channels or groups, is deleted.
- `capabilities.invalidated`

## Notes

- There is one built-in default space, "Vogi" (slug `vogi`). It is created on first use, users are added to it as members, and it cannot be deleted. Its view has `is_default: true`.
- `GET /audit-logs?space_id=…` lists management actions in a space. See [Extensibility](./extensibility.md).
- Space members are stored in `relationships`. `space_members` and `join_requests` are compatibility views.
