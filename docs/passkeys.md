# Passkeys

Base path: `/auth/passkeys`

## Endpoints

- `POST /auth/passkeys/register/start`
- `POST /auth/passkeys/register/finish`
- `POST /auth/passkeys/login/start`
- `POST /auth/passkeys/login/finish`
- `GET /auth/passkeys`
- `DELETE /auth/passkeys/{credential_id}`

## Notes

- Passkeys are an optional authentication path layered on top of the main auth module.
- The module depends on the WebAuthn stack being installed in the runtime environment.
