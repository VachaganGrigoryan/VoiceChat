# Search

Base path: `/search`

## Endpoints

- `GET /search/messages`
  Full-text search over messages the caller can read. Query: `q` (1–200 characters), `limit` (1–100, default 20), `cursor`.

## Notes

- The search covers only containers the caller can access, so a hit never leaks a container they cannot open.
- Results are ordered by recency, not by text relevance.
- Rate-limited to 30 requests per minute.
- People and resource search live in [Directory](./directory.md) and [Discovery](./discovery.md).
