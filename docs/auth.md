# Auth

Base path: `/auth`

## Endpoints

- `POST /auth/register`
  Request a verification code for a new account.
- `POST /auth/verify`
  Verify the code and receive access and refresh tokens.
- `POST /auth/login`
  Request a login verification code for an existing account.
- `POST /auth/refresh`
  Rotate tokens using a refresh token.
- `POST /auth/logout`
  Revoke a refresh token.

## Notes

- Authentication is email-code based.
- Access tokens are required for all protected modules.
- Verified users are required for messaging and most user-facing flows.
