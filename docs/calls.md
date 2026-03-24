# Calls

Base path: `/calls`

## Purpose

Calls provide 1:1 WebRTC signaling for audio and video calls. Media stays peer-to-peer in the client. The backend stores call lifecycle state, emits realtime call events, and returns STUN/TURN configuration.

## Endpoints

- `POST /calls`
  Create a new ringing call. Body: `callee_user_id`, `type`.
- `GET /calls/active`
  Return the current recoverable live call for the authenticated user, or `null`.
- `POST /calls/{call_id}/accept`
  Accept a ringing call. Body: `socket_id`.
- `POST /calls/{call_id}/reject`
  Reject a ringing call.
- `POST /calls/{call_id}/end`
  End or cancel a call depending on the current lifecycle state.

## REST Response Shape

- Create and accept return a `CallSession` payload with:
  `call`, `peer_user`, and `ice_servers`.
- `GET /calls/active` returns `CallSession | null`.
- Reject and end return the updated `CallDoc`.

## Call Statuses

- `ringing`
- `accepted`
- `connecting`
- `active`
- `reconnecting`
- `rejected`
- `cancelled`
- `expired`
- `ended`

## Socket Events

Client to server:

- `call.offer`
- `call.answer`
- `call.ice_candidate`
- `call.connected`
- `call.hangup`
- `call.reject`
- `call.resume`

Server to client:

- `call.incoming`
- `call.accepted`
- `call.rejected`
- `call.recovery_available`
- `call.offer`
- `call.answer`
- `call.ice_candidate`
- `call.connected`
- `call.reconnecting`
- `call.resumed`
- `call.ended`

## Notes

- Calls reuse the existing accepted-ping permission and block rules.
- A user can only participate in one live call at a time.
- Offline users can still be called; unanswered calls expire after `CALL_RING_TIMEOUT_SECONDS`.
- Active calls survive page refresh through a short reconnect grace window. A refreshed client should fetch `GET /calls/active` or wait for `call.recovery_available`, then emit `call.resume` and renegotiate WebRTC.
- If a participant does not reclaim a disconnected call before `CALL_RECONNECT_GRACE_SECONDS`, the backend ends the call and releases the live-call lock.
- `call.connected` is required so the backend can promote `connecting` calls to `active`.
- SDP and ICE candidates are relayed in realtime only and are not persisted.
- `reconnect_deadline_at` and `disconnected_user_ids` are included in the call payload so clients can show reconnect UI and countdown state.
- `CALL_SESSION_BACKEND` defaults to `memory`; use `redis` if you need cross-worker socket binding recovery.

## TURN / STUN Configuration

Relevant settings:

- `TURN_PROVIDER`
- `TURN_MULTI`
- `CALL_RING_TIMEOUT_SECONDS`
- `CALL_RECONNECT_GRACE_SECONDS`
- `CALL_SESSION_BACKEND`
- `CALL_SESSION_KEY_PREFIX`
- `CALL_STUN_URLS`
- `COTURN_URLS`
- `COTURN_USERNAME`
- `COTURN_PASSWORD`
- `CF_TURN_KEY_ID`
- `CF_TURN_API_TOKEN`
- `CF_ACCOUNT_ID`
- `CF_ACCOUNT_TOKEN`
- `CF_TURN_PAUSE_AT_GB`
- `CF_TURN_USAGE_LOOKBACK_DAYS`
- `CF_TURN_USAGE_CACHE_SECONDS`
- `TURN_TTL`

ICE server selection is env-driven. Call payloads and `/webrtc/ice-servers` use the same provider layer.
