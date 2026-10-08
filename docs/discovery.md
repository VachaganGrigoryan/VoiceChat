# Discovery

Base path: `/discovery`

## Purpose

Discovery is how two people find each other without already being connected: a short personal code, a shareable invite link, or a user search. Browsing people, channels, spaces and groups is handled by [Directory](./directory.md).

## Endpoints

- `POST /discovery/code/regenerate`
  Issue a new personal discovery code and invalidate the old one.
- `POST /discovery/code/resolve`
  Resolve a code into a user summary. Body: `code`.
- `POST /discovery/links`
  Create an invite link. Body: optional `expires_in_seconds`, `max_uses`.
- `GET /discovery/invite/{token}`
  Resolve an invite link token.
- `GET /discovery/users/search`
  Search users by `q`, limited with `limit`.

## Notes

- Responses respect profile privacy and blocks. The user search is the single implementation of those rules, and `GET /directory/people` delegates to it.
- Codes and links expire after `DISCOVERY_CODE_TTL_SECONDS` and `DISCOVERY_LINK_TTL_SECONDS`.
- User search skips private profiles and users who set `default_discovery_enabled: false` (see [Users](./users.md)).
