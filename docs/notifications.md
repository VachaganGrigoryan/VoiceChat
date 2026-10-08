# Notifications

Base path: `/notifications`

## Endpoints

- `GET /notifications`
  The caller's notifications. Query: `limit`.
- `GET /notifications/unread-count`
  The badge value, without paging the list.
- `POST /notifications/{notification_id}/read`
  Mark one as read. Idempotent. A notification that belongs to someone else returns `404`.
- `POST /notifications/read-all`
- `PATCH /notifications/preferences`
  Body: `dnd_from`, `dnd_to`, `timezone`, `notification_keywords`.
- `PATCH /notifications/conversations/{conversation_id}`
  Per-conversation level. Body: `notification_level`, `muted_until`.
- `POST /notifications/push-tokens`
  Register a push token. Body: `token`, `platform` (`ios`, `android` or `web`), optional `device_id`.
- `DELETE /notifications/push-tokens`
  Remove a push token. Query: `token` or `device_id`.

## Socket Events

- `notification_created`
  Sent to the recipient's `user:{id}` room.

## Notes

- Per-channel levels are set with `PATCH /channels/{channel_id}/notifications`. See [Channels](./channels.md).
- Push delivery goes through `PUSH_QUEUE_NAME` and `app/workers/push_worker.py`, which is not started by the combined worker. See [Architecture](./architecture.md#runtime).
