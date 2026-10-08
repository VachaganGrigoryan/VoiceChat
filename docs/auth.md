# Auth

Base path: `/auth`

## Endpoints

- `POST /auth/start`
  Start sign-in or sign-up and send a verification code. Body: `method`, `identifier`.
- `POST /auth/finish`
  Verify the code and receive `access_token` and `refresh_token`. Body: `method`, `identifier`, `code`.
- `POST /auth/refresh`
  Rotate tokens. Body: `refresh_token`.
- `POST /auth/logout`
  Revoke a refresh token. Body: `refresh_token`.

## Flow

1. `POST /auth/start` with `{ "method": "email", "identifier": "user@example.com" }`.
2. The API publishes a job to `EMAIL_QUEUE_NAME`. The worker emails a 6-digit code.
3. `POST /auth/finish` with `{ "method": "email", "identifier": "user@example.com", "code": "123456" }`.
4. A new account has no username until the client calls `PATCH /users/me/username`. See [Users](./users.md).

## Notes

- `email` is the only code-based method today. [Passkeys](./passkeys.md) are a separate, optional sign-in path under `/auth/passkeys`.
- Codes are stored hashed and expire. An invalid or expired code returns `400 CODE_INVALID`.
- Send the access token as `Authorization: Bearer <token>`. The same token authenticates the Socket.IO connection. See [Realtime](./realtime.md).
- Auth routes are rate-limited per client address.
