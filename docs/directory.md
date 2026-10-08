# Directory

Base path: `/directory`

## Purpose

The directory powers the client's Discover screen: public people, channels, spaces and groups, searchable from one place.

## Endpoints

- `GET /directory`
  A bounded preview across entity types. Query: `q`, `types`. Deliberately **unpaginated**, because there is no stable ordering across mixed types. Use the typed endpoints to see everything.
- `GET /directory/people`
  People search. Query: `q`, `limit`. A thin alias over `GET /discovery/users/search`, so privacy and block filtering have one implementation.
- `GET /directory/channels`
  Query: `q`, `owner_type`, `space_id`, `kind`, `tags`, `sort`, `cursor`, `limit`.
- `GET /directory/spaces`
  Query: `q`, `join_policy`, `sort`, `cursor`, `limit`.
- `GET /directory/groups`
  Query: `q`, `space_id`, `sort`, `cursor`, `limit`.

## Notes

- `sort` is one of `relevance`, `recent` or `popular`.
- Typed endpoints use cursor pagination and return `meta.next_cursor`.
- Only resources visible to the caller are listed. Visibility is decided by [Authorization](./authorization.md).
