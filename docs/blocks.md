# Blocks

Base path: `/blocks`

## Endpoints

- `POST /blocks/{user_id}`
  Block a user and revoke any pending or active connection.
- `DELETE /blocks/{user_id}`
  Unblock a user without recreating the connection.
- `GET /blocks`
  List blocked users with cursor pagination.

## Notes

- A block prevents connection requests, direct messages, typing, and calls in either direction.
- The block record is separate from the connection relationship.
