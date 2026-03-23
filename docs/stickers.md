# Stickers

Base path: `/stickers`

## Scope

- v1 supports owner-authored sticker packs only.
- Stickers are stored in object storage and referenced from messages by sticker metadata only.
- Sticker records persist `storage` plus storage keys, so sticker assets follow the same backend storage concept as message media.
- Message sending uses `POST /messages/sticker`; sticker files are not uploaded through `/messages/media`.
- Message responses hydrate sticker asset URLs into `media` so clients can render stickers without a second backend request.

## Pack Endpoints

- `POST /stickers/packs`
  Create a new sticker pack.
- `GET /stickers/packs/my`
  List the current user’s packs.
- `GET /stickers/packs/{pack_id}`
  Get one owned pack with its stickers.
- `PATCH /stickers/packs/{pack_id}`
  Update owned pack metadata.
- `POST /stickers/packs/{pack_id}/publish`
  Publish a draft pack. Requires at least one active sticker.
- `DELETE /stickers/packs/{pack_id}`
  Soft-delete a pack from catalog views.

## Sticker Upload Flow

1. `POST /stickers/packs/{pack_id}/stickers/upload`
   Request an upload session and upload target.
2. Upload the file:
   - `s3`: use the presigned `PUT` target returned by the API.
   - `local`: `PUT /stickers/uploads/{upload_session_id}/content`.
3. `POST /stickers/uploads/{upload_session_id}/complete`
   Validate, normalize, thumbnail, and create the sticker record.

## Sticker Endpoints

- `PATCH /stickers/{sticker_id}`
  Update slug, title, emoji aliases, sort order, or active/blocked status.
- `DELETE /stickers/{sticker_id}`
  Soft-delete a sticker by archiving it.
- `POST /stickers/resolve`
  Resolve sticker ids into playable asset URLs.
- `GET /stickers/search?emoji=...`
  Search owned stickers by emoji alias.
- `GET /stickers/by-ref/{pack_slug}/{sticker_slug}`
  Lookup an owned sticker by canonical reference.

## Validation

- only static `image/webp` stickers are accepted in v1
- max file size is `512 KB`
- max dimensions are `512x512`
- uploads are sanitized by re-encoding and thumbnail generation
- sticker asset URLs prefer `CDN_BASE_URL` for `s3` storage when configured

## Message Shape

Sticker messages return:

```json
{
  "type": "sticker",
  "media": {
    "storage": "local",
    "key": "stickers/.../original.webp",
    "url": "/media/stickers/.../original.webp",
    "mime": "image/webp",
    "size_bytes": 12345,
    "duration_ms": null
  },
  "sticker": {
    "sticker_id": "...",
    "pack_id": "...",
    "pack_slug": "funny_cats",
    "sticker_slug": "party_cat",
    "emoji": "🎉",
    "version": 1
  }
}
```
