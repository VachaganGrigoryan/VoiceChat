# Users

Base path: `/users`

## Endpoints

- `GET /users/me`
  Return the current user profile.
- `PATCH /users/me`
  Update profile fields such as display name, bio, and privacy settings.
- `PATCH /users/me/username`
  Update the username.
- `PATCH /users/me/avatar`
  Upload a new avatar file.
- `DELETE /users/me/avatar`
  Remove the current avatar and delete the stored file.

## Notes

- User endpoints require authentication.
- Avatars are stored through the configured storage backend.
