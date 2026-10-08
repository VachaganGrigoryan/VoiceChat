# Configuration

## Purpose

All settings are read by `app/core/config.py` from environment variables, with `.env` as the default file (`ENV_FILE` selects another). `.env.example` is a starting point for local development, and `.env.test` configures the `api_test` service. Never commit real secrets.

This page lists variable **names and purpose only**. Defaults are in `app/core/config.py`.

## Application

- `APP_ENV`
  Environment name, for example `dev` (default) or `test`.
- `CORS_ALLOWED_ORIGINS`
  Comma-separated list of allowed browser origins.
- `WEB_APP_NAME`, `WEB_APP_URL`
  Used in emails and invite links.
- `RATE_LIMIT_ENABLED`, `RATE_LIMIT_STORAGE_URI`
  Toggle and Redis storage for rate limiting.

## Database

- `MONGO_URI`, `MONGO_DB`
- `MONGO_SERVER_SELECTION_TIMEOUT_MS`, `MONGO_MAX_POOL_SIZE`, `MONGO_MIN_POOL_SIZE`

## Auth and passkeys

- `JWT_SECRET`, `JWT_ALG`
- `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`
- `PASSKEY_RP_ID`, `PASSKEY_RP_NAME`, `PASSKEY_ORIGIN`, `PASSKEY_CHALLENGE_TTL_SECONDS`
- `DISCOVERY_CODE_TTL_SECONDS`, `DISCOVERY_LINK_TTL_SECONDS`

## Email

- `EMAIL_PROVIDER`
  `mock` (default) or `smtp`. The test environment uses `mock`.
- `EMAIL_QUEUE_NAME`
  RabbitMQ queue for verification emails. The API publishes to it and the worker consumes it.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`

## Queues, Redis and realtime

- `RABBITMQ_URL`, `PUSH_QUEUE_NAME`
- `REDIS_URL`
- `PRESENCE_BACKEND`, `PRESENCE_KEY_PREFIX`
  `memory` or `redis`.
- `SOCKETIO_QUEUE_BACKEND`, `SOCKETIO_REDIS_URL`
  Use the Redis manager whenever there is more than one API process, or a worker needs to emit.

## Storage

- `STORAGE_PROVIDER`
  `local` (default) or `s3` for S3-compatible storage. MinIO runs locally in `docker-compose.yml`.
- `UPLOAD_DIR`, `MAX_FILE_SIZE_MB`

## Calls and TURN

- `CALL_RING_TIMEOUT_SECONDS`, `CALL_RECONNECT_GRACE_SECONDS`
- `CALL_SESSION_BACKEND`, `CALL_SESSION_KEY_PREFIX`
  `memory` (default) or `redis`.
- `CALL_STUN_URLS`, `CALL_TURN_URLS`
- `TURN_PROVIDER`
  `coturn` or `cloudflare`.
- `TURN_MULTI`
  Add the other provider as a fallback.
- `TURN_REALM`, `TURN_AUTH_SECRET`, `TURN_CREDENTIAL_TTL_SECONDS`, `TURN_TTL`
- `COTURN_URLS`, `COTURN_USERNAME`, `COTURN_PASSWORD`
- `CF_TURN_KEY_ID`, `CF_TURN_API_TOKEN`, `CF_ACCOUNT_ID`, `CF_ACCOUNT_TOKEN`
- `CF_TURN_PAUSE_AT_GB`, `CF_TURN_USAGE_LOOKBACK_DAYS`, `CF_TURN_USAGE_CACHE_SECONDS`

How these combine is described in [WebRTC](./webrtc.md) and [Calls](./calls.md).

## Notes

- The calls and TURN variables are not in `.env.example`. Add them to your local `.env` when you work on calls.
- In `.env.test`, `EMAIL_PROVIDER=mock` and `EMAIL_QUEUE_NAME=email.send.test`. The compose `worker` consumes `email.send`, so the test API's verification emails are only delivered to MailHog if a second email worker consumes the test queue. See [Testing](./testing.md).
