# Documentation

## Start here

- [Architecture](./architecture.md): runtime, code layout, the container model, HTTP conventions, realtime
- [Authorization](./authorization.md): permissions, resolution order, roles, capabilities
- [Configuration](./configuration.md): environment variables
- [Testing](./testing.md): running the suites inside Docker, test email delivery, OpenAPI

## API reference

Schemas for every route are in `openapi.json`, browsable at `/docs` on a running API.

| Area | Page | Base paths |
|---|---|---|
| Sign-in | [Auth](./auth.md) | `/auth` |
| | [Passkeys](./passkeys.md) | `/auth/passkeys` |
| People | [Users](./users.md) | `/users` |
| | [Relationships](./relationships.md) | `/connections`, `/follows`, follow and membership routes |
| | [Blocks](./blocks.md) | `/blocks` |
| | [Discovery](./discovery.md) | `/discovery` |
| | [Directory](./directory.md) | `/directory` |
| Containers | [Conversations](./conversations.md) | `/conversations` |
| | [Spaces](./spaces.md) | `/spaces` |
| | [Channels](./channels.md) | `/channels` |
| Content | [Messages](./messages.md) | `/messages` |
| | [Feeds](./feeds.md) | `/feeds` |
| | [Saved messages](./saved.md) | `/me/saved-messages` |
| | [Polls](./polls.md) | `/polls` |
| | [Search](./search.md) | `/search` |
| Delivery | [Realtime](./realtime.md) | `/realtime` and Socket.IO events |
| | [Notifications](./notifications.md) | `/notifications` |
| | [Devices](./devices.md) | `/devices` |
| Calls | [Calls](./calls.md) | `/calls` |
| | [WebRTC](./webrtc.md) | `/webrtc` |
| Platform | [Extensibility and moderation](./extensibility.md) | `/webhooks`, `/slash-commands`, `/reports`, `/audit-logs` |
| | [Health](./health.md) | `/health` |
| Access | [Authorization](./authorization.md) | roles, `/viewer/capabilities`, `/relationships/{id}/roles` |
