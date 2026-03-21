# Discovery

Base path: `/discovery`

## Endpoints

- `POST /discovery/code/regenerate`
  Regenerate the current user discovery code.
- `POST /discovery/code/resolve`
  Resolve a discovery code into a user summary.
- `POST /discovery/links`
  Create an invite link with expiry and usage controls.
- `GET /discovery/invite/{token}`
  Resolve an invite token.
- `GET /discovery/users/search`
  Search users by query.

## Notes

- Discovery responses are privacy-aware.
- Invite links and codes are intended to be revocable and time-bound.
