# Connections

Base path: `/connections`

## Purpose

Connection relationships are the chat-permission layer. An active connection is required before direct messaging is allowed. The product UI may continue to call a connection request a Ping.

## Endpoints

- `POST /connections/{user_id}/ping`
  Create or reopen a connection request.
- `GET /connections/pending`
  List pending connection requests. Filter with `direction=incoming|outgoing`.
- `GET /connections`
  List active connections with peer and direct-conversation summaries.
- `POST /connections/{relationship_id}/accept`
  Accept an incoming pending connection.
- `POST /connections/{relationship_id}/decline`
  Decline an incoming pending connection.
- `DELETE /connections/{relationship_id}`
  Revoke a pending or active connection.

## Notes

- Collection endpoints use cursor pagination with `limit` and `cursor`.
- Messaging, typing, and calls enforce active-connection permission.
- Deleting direct-message history does not revoke the connection.
- Blocking revokes the current connection. Unblocking does not recreate it.
