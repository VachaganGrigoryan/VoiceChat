# Auth

Base path: `/auth`

## Endpoints

- `POST /auth/start`
  Start auth for a supported method and request a verification code.
- `POST /auth/finish`
  Verify the code and receive access and refresh tokens.
- `POST /auth/refresh`
  Rotate tokens using a refresh token.
- `POST /auth/logout`
  Revoke a refresh token.

## Notes

- Authentication is method-based with `email` as the only supported method today.
- `start` accepts `{ "method": "email", "identifier": "user@example.com" }`.
- `finish` accepts `{ "method": "email", "identifier": "user@example.com", "code": "123456" }`.
- Access tokens are required for all protected modules.
- Verified users are required for messaging and most user-facing flows.
