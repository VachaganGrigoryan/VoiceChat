# Polls

Base path: `/polls`

## Purpose

Polls are posted into a conversation or channel by the built-in **PollBot** user (`app/bots/`). The poll itself is stored in `polls`, and a bot message in the container references it.

## Endpoints

- `POST /polls`
  Create a poll. Body: `container_type`, `container_id`, `question`, `options`, optional `allows_multiple`, `anonymous`, `closes_at`, `results_visibility` (`after_vote`, `always` or `after_close`).
- `GET /polls/{poll_id}`
- `POST /polls/{poll_id}/vote`
  Body: `option_ids`.
- `POST /polls/{poll_id}/retract`
  Withdraw the caller's vote.
- `POST /polls/{poll_id}/close`
  Close the poll early.

## Socket Events

- `poll_updated`
  Sent to the container's participants when votes change or the poll closes.

## Notes

- Creating a poll needs `poll.create`, and closing someone else's poll needs `poll.manage`.
- In groups, `allow_member_polls` (`PATCH /conversations/{conversation_id}/settings`) controls whether ordinary members may create polls.
- Polls with `closes_at` are closed automatically by the worker's poll auto-close loop.
- Built-in bot users are seeded by `app/bots/seed.py`.
