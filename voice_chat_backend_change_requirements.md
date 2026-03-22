# Voice Chat Backend — Full Change Requirements

## 1. Purpose

Expand the current Voice Chat backend from a voice-only realtime messaging system into a more complete private messaging platform with:

- richer user identity
- profile management
- multiple message types
- private-by-default discovery controls
- ping-based chat permissioning
- unified discovery tokens for codes and invite links
- stronger inbox and contact features

The new design must preserve the current strengths of the project:

- FastAPI + Socket.IO architecture
- modular backend design
- Redis-backed realtime support
- RabbitMQ workers
- S3/MinIO presigned uploads
- strong testability
- horizontal scalability readiness

---

## 2. Product Goals

### Primary goals
- allow users to have a real identity beyond email and internal id
- allow users to discover each other safely
- prevent unsolicited direct messaging
- expand messaging beyond voice-only communication
- support future social and chat features without redesigning the core data model

### Non-goals for this phase
- no group chats yet
- no public social feed
- no push notification provider integration yet
- no video messaging implementation yet
- no full reaction/sticker marketplace yet

---

## 3. High-Level Functional Changes

The backend must be extended in the following areas:

1. user identity and profile management
2. generic message model with new message types
3. ping system for chat permission
4. unified discovery token system
5. search and privacy rules
6. chats, contacts, and inbox views
7. realtime event expansion
8. database and index updates
9. testing expansion

---

## 4. User Model Enhancements

## Current state
Current user data is minimal and mainly contains:
- id
- email
- is_verified
- timestamps

## Required changes
Add support for richer user identity and profile data.

### New user fields
- `username`
- `display_name`
- `bio`
- `avatar_url`
- `is_private`
- `last_seen_at`
- `username_updated_at`
- `default_discovery_enabled`

### Username requirements
- username must be unique
- username must be searchable
- username must be auto-generated during registration
- user must be allowed to update username later
- validation rules must exist for allowed characters and length
- reserved usernames must be blocked

### Username generation
At registration, the system must generate a cool unique username automatically.

Suggested style:
- adjective + noun + number
- short readable names
- no offensive words
- deterministic uniqueness fallback if collision happens

Examples:
- `silent-fox-27`
- `blue-orbit-84`
- `swift-panda-13`

### Profile update requirements
Authenticated users must be able to update:
- display name
- bio
- avatar
- privacy settings
- username (subject to cooldown or validation policy)

### Privacy behavior
- email must remain private
- internal id must not be used as public identity
- private users must not appear in open public discovery

---

## 5. Generic Message System

## Current state
The current message model is voice-focused and stores voice-specific data.

## Required change
Refactor messaging into a generic message model that supports multiple content types while keeping voice fully supported.

### Required message types
- `voice`
- `text`
- `image`
- `emoji`
- `sticker`
- `video` later

### Mandatory support in this phase
Must implement now:
- voice
- text with full UTF-8 support
- image
- emoji
- sticker

Video should be planned in the model but can remain unimplemented.

### Message schema direction
The message document must evolve to support type-based payloads.

Suggested fields:
- `id`
- `sender_id`
- `receiver_id`
- `message_type`
- `text`
- `media_url`
- `metadata`
- `status`
- `created_at`
- `updated_at`
- `edited_at`
- `deleted_at`

### Field behavior
- `text` should be used for text and may also be used for captions later
- `media_url` should be used for voice/image/video/sticker payload references when applicable
- `metadata` should hold type-specific structured information
- `status` should preserve delivery lifecycle

### UTF-8 requirement
Text messages must correctly support:
- Armenian
- English
- Russian
- emoji
- mixed unicode text
- newline-safe and JSON-safe content

### Image and sticker handling
- image uploads should reuse presigned upload flow
- sticker messages may use stored sticker asset ids or URLs
- image and sticker delivery must use the same permission checks as voice/text messages

### Backward compatibility
- existing voice message flow must continue to work
- current voice endpoints may remain initially, but service logic should converge into a generic message architecture

---

## 6. Ping System (Permission to Chat)

## Goal
Users must not be able to directly message arbitrary users.
Messaging between two users must only be allowed after a mutual contact permission flow.

## New concept
Introduce `Ping` as a bilateral permission request.

### Flow
1. user A discovers user B
2. user A sends a ping
3. user B accepts or declines
4. only after acceptance can both users exchange messages

### Ping rules
- one active ping relationship per user pair
- accepted ping grants bidirectional messaging permission
- sender cannot spam repeated pings while a pending one exists
- both incoming and outgoing ping history must be visible
- future support for block and cancel should be possible

### Ping statuses
- `pending`
- `accepted`
- `declined`
- `cancelled`
- `expired` optional
- `blocked` reserved for future

### Ping collection fields
- `id`
- `from_user_id`
- `to_user_id`
- `status`
- `created_at`
- `updated_at`
- `responded_at`

### Permission enforcement
All message send operations must validate that:
- sender and receiver have an accepted ping relationship
- or another explicit chat permission exists in the future

This rule must apply to:
- REST message creation
- Socket.IO message events
- voice
- text
- image
- emoji
- sticker

---

## 7. Unified Discovery Token System

## Problem
The earlier design had separate concepts for:
- six-digit discovery codes
- invite links

These should be merged because they represent the same core capability: a controlled way to discover a private user.

## Required change
Create a unified `discovery_tokens` model.

This model must support two delivery forms:
- plain code form
- link form

### Supported token types
- `code`
- `link`

### Use cases
#### Code token
- short token
- user shares it manually
- another user enters it in search
- useful for private direct discovery

#### Link token
- longer token
- embedded into a shareable URL
- can be sent outside the platform
- useful for one-time or limited invite flow

### Discovery token fields
- `id`
- `user_id`
- `type`
- `token_hash`
- `token_preview` optional
- `expires_at`
- `used_at` nullable
- `max_uses` nullable
- `use_count`
- `is_active`
- `created_at`
- `updated_at`

### Security requirements
- raw tokens should not be stored if avoidable
- token lookup should use secure comparison / hashing strategy
- expired tokens must be rejected
- inactive tokens must be rejected
- usage limits must be enforced

### Product rules
- each user should have at most one active manual discovery code at a time
- users may have multiple invite links
- invite links may be one-time or limited-use
- discovery codes should be rotatable
- private users may still be discoverable through valid discovery tokens

### Invite URL behavior
A link token should resolve through a dedicated route and allow:
- viewing the minimal target profile
- sending a ping
- optionally requiring authentication before completing the ping

---

## 8. Search and Discovery Rules

## Goal
Allow users to find each other safely without exposing every account publicly.

### Search modes
The system must support:
- search by username
- search by discovery code
- search by invite link token resolution

### Privacy rules
- public users may appear in username search
- private users must not appear in general username search unless policy explicitly allows it
- valid discovery token lookup may bypass general search visibility
- email must never be used as a public search field

### Search result visibility
Search results should expose minimal safe data, such as:
- username
- display name
- avatar
- online/offline status if permitted
- whether ping can be sent

Sensitive data must not be exposed.

### Discovery throttling
Rate limits must apply to:
- username search
- discovery code checks
- ping creation attempts

---

## 9. Chats, Contacts, and Inbox

## Goal
Expand from raw message history to user-facing conversation views.

### Required views
- recent chats
- incoming pings
- outgoing pings
- accepted contacts
- online users only
- offline users only
- recent message previews

### Recent chats requirements
Each chat list item should be able to provide:
- other user identity summary
- last message preview
- unread count
- last activity timestamp
- online/offline presence

### Contacts requirements
A contact is defined by an accepted ping relationship.

Contacts list should support:
- pagination
- recent activity ordering
- online/offline filtering
- profile summary response

### Message history requirements
Conversation history must support:
- paging
- filtering by peer user
- generic message payload rendering
- future extensibility for edits/deletes

---

## 10. Presence Enhancements

## Current state
Presence exists with in-memory and Redis implementations.

## Required changes
Presence should continue to support current behavior and additionally expose:
- online/offline state
- last seen timestamp
- presence summary in user search and chat results where allowed

### Rules
- exact live presence should be lightweight
- offline users may expose `last_seen_at` depending on privacy rules
- presence aggregation must remain compatible with multi-instance deployment

---

## 11. Realtime Event Expansion

## Goal
Socket.IO layer must be extended for the new product model.

### Client → Server events to support
- `send_voice_message`
- `send_text_message`
- `send_image_message`
- `send_emoji_message`
- `send_sticker_message`
- `typing_start`
- `typing_stop`
- `voice_message_delivered`
- `voice_message_read`
- generic message delivered/read equivalents if architecture is unified
- `send_ping`
- `accept_ping`
- `decline_ping`

### Server → Client events to support
- `receive_voice_message`
- `receive_text_message`
- `receive_image_message`
- `receive_emoji_message`
- `receive_sticker_message`
- `voice_message_status`
- generic message status event if architecture is unified
- `presence_update`
- `typing_start`
- `typing_stop`
- `ping_received`
- `ping_accepted`
- `ping_declined`
- `chat_permission_updated`

### Realtime rules
- user room design should remain `user:{id}`
- all send events must validate chat permission first
- all delivery fanout must remain compatible with Redis pub/sub adapter
- event payloads should use consistent schemas

---

## 12. API Requirements

## User/Profile APIs
Add endpoints for profile and privacy management.

Suggested endpoints:
- `GET /users/me`
- `PATCH /users/me`
- `PATCH /users/me/username`
- `PATCH /users/me/privacy`

## Search/Discovery APIs
Suggested endpoints:
- `GET /users/search?q=...`
- `POST /discovery/code/regenerate`
- `POST /discovery/links`
- `GET /invite/{token}`

## Ping APIs
Suggested endpoints:
- `POST /pings`
- `GET /pings/incoming`
- `GET /pings/outgoing`
- `POST /pings/{id}/accept`
- `POST /pings/{id}/decline`
- `POST /pings/{id}/cancel` optional

## Message APIs
Refactor/add endpoints to support generic message creation and retrieval.

Suggested endpoints:
- `POST /messages/text`
- `POST /messages/image/upload-url`
- `POST /messages/image`
- `POST /messages/sticker`
- keep `POST /messages/upload-url` for voice if needed during transition
- keep `POST /messages/voice`
- `GET /messages/history`
- `GET /chats`
- `GET /contacts`

## Presence APIs
Suggested endpoints:
- `GET /realtime/online-users`
- `GET /realtime/presence`
- `GET /presence/status/{user_id}` optional

---

## 13. Database and Index Requirements

## Users
Need unique indexes for:
- username

May need indexes for:
- is_private
- last_seen_at

## Messages
Need indexes for:
- sender_id
- receiver_id
- created_at
- conversation query pattern if conversation ids are introduced later
- status where appropriate

## Pings
Need indexes for:
- from_user_id
- to_user_id
- status
- unique active pair constraint strategy

## Discovery Tokens
Need indexes for:
- user_id
- type
- expires_at
- is_active

TTL or cleanup strategy may be used where appropriate.

---

## 14. Service and Module Design Requirements

The implementation must stay aligned with the current modular project architecture.

### New or expanded modules expected
- `modules/users/` or equivalent profile module
- `modules/discovery/`
- `modules/pings/`
- expanded `modules/messages/`
- expanded `modules/realtime/`

### Layering expectations
Each module should continue to follow the current style:
- router
- service
- repository
- schemas

### Shared logic
Discovery token generation and validation should live in a shared dedicated service inside the discovery module, not duplicated across routers.

Permission validation for messaging should live in service-level logic and be reusable from both REST and socket handlers.

---

## 15. Security Requirements

### Must-have
- no direct messaging without accepted ping
- no public exposure of email
- private users hidden from broad search
- discovery tokens must expire or be revocable
- invite links must respect usage limits
- search and ping endpoints must be rate-limited
- uploads remain presigned and private
- authorization checks must apply consistently across REST and Socket.IO

### Additional recommendations
- add reserved word protection for usernames
- add abusive ping throttling
- add audit-friendly timestamps
- prepare future support for blocking/reporting users

---

## 16. Backward Compatibility and Migration Requirements

### Must preserve
- current auth flow
- current refresh token rotation model
- current voice upload flow
- current presence architecture
- current worker/email verification behavior

### Migration direction
- evolve messages model without breaking voice support
- introduce new user fields with safe defaults
- backfill usernames for existing users if needed
- keep legacy endpoints during transition if frontend still depends on them

---

## 17. Testing Requirements

The new scope must include both unit and integration test coverage.

### Unit tests
Need tests for:
- username generation
- username validation
- discovery token generation and validation
- ping permission rules
- message type validation
- privacy visibility rules

### Integration tests
Need tests for:
- register flow with username generation
- profile update flow
- username search flow
- private user visibility rules
- discovery code lookup flow
- invite link resolution flow
- ping creation / accept / decline flow
- text message send flow
- image message flow
- socket permission enforcement
- recent chats response
- contacts response

### Realtime tests
Need tests for:
- ping events
- text message delivery
- image/sticker event routing
- permission denied behavior when ping not accepted
- presence propagation still working

---

## 18. Suggested Implementation Order

### Phase 1
User profile and username foundation.
- add user fields
- implement username generation
- implement profile endpoints

### Phase 2
Generic messages foundation.
- refactor message model
- add text support
- keep voice support intact

### Phase 3
Ping permission system.
- create ping model
- enforce permission checks
- add ping APIs and realtime events

### Phase 4
Discovery system.
- add unified discovery tokens
- add discovery code and invite link flows
- add privacy-aware search

### Phase 5
Inbox and contact views.
- recent chats
- contacts
- unread counts if introduced now or later

### Phase 6
Image and sticker completion.
- presigned upload support
- payload delivery
- tests

---

## 19. MVP Scope Recommendation

For the next implementation milestone, focus on the smallest high-value set:

- username generation and update
- user profile endpoints
- text message support with UTF-8
- ping system
- private/public search behavior
- unified discovery tokens
- recent chats and ping listing

This gives the product a major functional leap while keeping complexity controlled.

---

## 20. Final Acceptance Criteria

The change set is considered complete when:

- users have unique usernames and editable profiles
- users can search safely according to privacy rules
- discovery codes and invite links are implemented through one unified token model
- messaging is no longer voice-only and supports text with UTF-8
- accepted ping is required before two users can chat
- recent chats and ping lists are available
- realtime events support the new product behavior
- tests cover the main happy paths and critical permission failures
- the architecture remains modular and consistent with the current codebase

