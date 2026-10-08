# Devices

Base path: `/devices`

## Purpose

Device registration and pre-key distribution: the groundwork for end-to-end encrypted messaging. A device publishes identity and signing keys plus a supply of one-time pre-keys, and a peer fetches a pre-key bundle to start an encrypted session.

## Endpoints

- `POST /devices`
  Register or update a device. Body: `device_id`, optional `name`, `platform`, `registration_id`, `identity_public_key`, `signing_public_key`.
- `POST /devices/{device_id}/prekeys`
  Upload one-time pre-keys. Body: `prekeys`.
- `GET /users/{user_id}/prekey-bundle`
  Fetch a pre-key bundle for one of the user's devices. Query: optional `device_id`.

## Notes

- Private keys never reach the server.
- Push tokens are registered separately. See [Notifications](./notifications.md).
