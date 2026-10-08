# Extensibility and moderation

Routes defined in `app/modules/extensibility/router.py`.

## Webhooks

- `GET /webhooks`
  List webhooks. Query: `conversation_id` or `space_id`.
- `POST /webhooks`
  Create a webhook. Body: `direction` (`incoming` or `outgoing`), `target_type` (`conversation` or `space`), `target_id`, optional `url` and `events`.

## Slash commands

- `GET /slash-commands`
- `POST /slash-commands`
  Register a command. Body: `trigger`, `handler_url`, optional `description`.

## Reports

- `POST /reports`
  Report content. Body: `target_type` (`message`, `user` or `conversation`), `target_id`, `reason`.
- `GET /reports`
  List reports. Query: `status`.
- `PATCH /reports/{report_id}/resolve`
  Body: `status` (`resolved` or `dismissed`).

## Audit logs

- `GET /audit-logs`
  Management actions recorded for a space. Query: `space_id`.

## Notes

- These routes are not tagged in `openapi.json`, so they appear under "default" in Swagger UI.
- The web client deliberately has no reporting UI yet. These endpoints exist for future clients and integrations.
