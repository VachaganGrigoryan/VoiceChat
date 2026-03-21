# Pings

Base path: `/pings`

## Purpose

Pings are the chat-permission layer. An accepted ping is required before direct messaging is allowed.

## Endpoints

- `POST /pings`
  Create a new ping.
- `GET /pings/incoming`
  List incoming pings.
- `GET /pings/outgoing`
  List outgoing pings.
- `POST /pings/{ping_id}/accept`
  Accept a pending ping.
- `POST /pings/{ping_id}/decline`
  Decline a pending ping.
- `POST /pings/{ping_id}/cancel`
  Cancel an owned pending ping.
- `POST /pings/block`
  Block a peer pair.
- `POST /pings/unblock`
  Remove a block created by the current user.
- `GET /pings/blocked`
  List blocked peer pairs for the current user.

## Notes

- Messaging and typing both enforce accepted ping permission.
- Blocking overrides accepted permission.
