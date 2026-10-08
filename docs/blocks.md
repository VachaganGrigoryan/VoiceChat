# Blocks

Base path: `/blocks`

## Endpoints

- `POST /blocks/{user_id}`
  Block a user and revoke any pending or active connection.
- `DELETE /blocks/{user_id}`
  Unblock a user without recreating the connection.
- `GET /blocks`
  List blocked users with cursor pagination (`limit`, `cursor`).

## Socket Events

- `block.created`
- `block.removed`

## Notes

- A block works in both directions. It prevents connection requests, direct messages, typing and calls either way.
- The block record is separate from the connection in [Relationships](./relationships.md).
- Authorization checks for a block before it checks ownership, membership or roles. See [Authorization](./authorization.md#resolution-order).
