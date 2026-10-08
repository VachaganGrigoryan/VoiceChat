# Saved messages

Base path: `/me/saved-messages`

## Endpoints

- `GET /me/saved-messages`
  The caller's saved messages, newest first. Query: `limit`.
- `POST /me/saved-messages`
  Save a message. Body: `container_type`, `container_id`, `message_id`.
- `DELETE /me/saved-messages/{message_id}`
  Remove a saved message.

## Notes

- Saved messages are private to the caller.
- The web client shows them as the **Saved** tab of the feed.
