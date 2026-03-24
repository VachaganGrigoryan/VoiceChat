# WebRTC

Base path: `/webrtc`

## Endpoint

- `GET /webrtc/ice-servers`
  Returns authenticated ICE server configuration in `data.iceServers`.

## Provider Selection

- `TURN_PROVIDER=coturn` uses static coturn credentials from `COTURN_URLS`, `COTURN_USERNAME`, and `COTURN_PASSWORD`.
- `TURN_PROVIDER=cloudflare` generates short-lived TURN credentials from Cloudflare using `CF_TURN_KEY_ID`, `CF_TURN_API_TOKEN`, and `TURN_TTL`.
- `TURN_MULTI=true` adds the other provider as fallback when it is configured.
- `CF_TURN_PAUSE_AT_GB=999` stops issuing Cloudflare TURN credentials once recent Cloudflare TURN egress reaches the threshold.
- Automatic pausing requires `CF_ACCOUNT_ID` and a Cloudflare API token `CF_ACCOUNT_TOKEN` that can call both TURN credential generation and Account Analytics.
- Usage checks are cached for `CF_TURN_USAGE_CACHE_SECONDS` and measured over `CF_TURN_USAGE_LOOKBACK_DAYS`.

## Notes

- The frontend stays provider-agnostic and should use the returned `iceServers` as-is.
- Cloudflare API tokens never go to the client.
- `CALL_STUN_URLS` are prepended to the provider TURN servers when configured.
- The Cloudflare guard uses TURN analytics only. Cloudflare documents the free 1,000 GB/month tier across both TURN and SFU, so if you add SFU later you should account for that shared quota too.
