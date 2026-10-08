# Passkeys

Base path: `/auth/passkeys`

## Endpoints

- `POST /auth/passkeys/register/start`
  Begin registering a passkey for the signed-in user. Body: optional `nickname`.
- `POST /auth/passkeys/register/finish`
  Complete registration. Body: `credential`, optional `nickname`.
- `POST /auth/passkeys/login/start`
  Begin a passkey sign-in. Body: optional `email`.
- `POST /auth/passkeys/login/finish`
  Complete sign-in and receive tokens. Body: `credential`, optional `email`.
- `GET /auth/passkeys`
  List the current user's passkeys.
- `DELETE /auth/passkeys/{credential_id}`
  Remove a passkey.

## Notes

- Passkeys are WebAuthn credentials layered on top of [Auth](./auth.md). Registration needs an authenticated user, and login returns the same token pair as `POST /auth/finish`.
- The relying party is configured with `PASSKEY_RP_ID`, `PASSKEY_RP_NAME` and `PASSKEY_ORIGIN`. `PASSKEY_ORIGIN` must match the web client's origin exactly.
- Challenges expire after `PASSKEY_CHALLENGE_TTL_SECONDS`.
