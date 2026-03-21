# VoiceChat Backend

FastAPI and Socket.IO backend for direct messaging, presence, ping-based chat permissions, discovery flows, and
passkeys.

## Status

- The `docs/` directory is the current documentation source.
- Older standalone markdown files in the repository root may be historical and can drift from the code.

## Documentation

- [Docs Index](./docs/index.md)
- [Auth](./docs/auth.md)
- [Users](./docs/users.md)
- [Pings](./docs/pings.md)
- [Messages](./docs/messages.md)
- [Realtime](./docs/realtime.md)
- [Discovery](./docs/discovery.md)
- [Passkeys](./docs/passkeys.md)

## Architecture

Core services:

- FastAPI API
- Socket.IO realtime server
- MongoDB for persistent application data
- Redis for presence and rate limiting
- RabbitMQ for background jobs
- Local or S3-compatible object storage for media

## Local Development

- Install dependencies with Poetry.
- Start required infrastructure from `docker-compose.yml`.
- Run the API with `uvicorn app.main:app --reload`.

## Messaging Notes

- Sending messages requires an accepted ping.
- Typing events and compatibility socket send acknowledgements enforce the same permission rule.
- Owner deletion hard-deletes the message and removes stored media when present.
- Peer deletion hides the message only for that user.

## Overview

This project implements a realtime voice messaging backend using **FastAPI**, **Socket.IO**, **MongoDB**, **Redis**, **RabbitMQ**, and **S3/MinIO**.

The system allows authenticated users to upload voice messages and deliver them instantly to other users via WebSocket connections.

The architecture is designed to demonstrate **senior-level backend design**, including realtime communication, horizontal scalability, security controls, and background processing.

---

# Architecture

```
Client
  │
  ▼
FastAPI API
  │
  ├── MongoDB (users + messages)
  ├── S3 / MinIO (voice files)
  ├── Redis
  │      ├── Socket.IO pub/sub
  │      ├── presence registry
  │      └── rate limiting
  │
  └── RabbitMQ
           │
           ▼
        Workers
           │
           ▼
         Mailhog
```

---

# Core Features

## Authentication

Users authenticate via REST endpoints.

Features:

- user registration
- email verification
- login with token
- authenticated socket connection

Authentication tokens are validated when establishing a Socket.IO connection.

### Access and Refresh Tokens

The authentication system uses a **dual‑token model**:

- **Access Token** – short‑lived JWT used for API and Socket.IO authentication.
- **Refresh Token** – long‑lived opaque token used to obtain new access tokens.

Access token properties:

- JWT signed with server secret
- contains user id (`sub` claim)
- short expiration (e.g. 60 minutes)
- validated statelessly by the API

Refresh token properties:

- randomly generated secure string
- stored in database **only as a hash**
- long expiration (e.g. 7 days)
- rotated on every refresh request

Example response:

```json
{
  "access_token": "JWT...",
  "refresh_token": "random_secure_token",
  "token_type": "bearer"
}
```

This design provides:

- stateless request authentication
- secure session revocation
- refresh token rotation
- protection against token replay attacks

---

# Voice Message Flow

```
Client
  │
  │ upload voice
  ▼
FastAPI
  │
  ├── generate presigned upload URL
  ├── client uploads file to S3
  ├── message stored in MongoDB
  │
  ▼
Socket.IO
  │
  ▼
Receiver receives message instantly
```

Steps:

1. client requests upload URL
2. server returns presigned URL
3. client uploads voice file
4. server stores message metadata
5. socket event notifies receiver

---

# Realtime System

## Socket Connection

Clients connect using Socket.IO.

Authentication is required during the handshake.

Upon connection:

- user session is stored
- user joins room `user:{id}`
- presence system marks user online

---

## User Rooms

Each user has a dedicated room:

```
user:{user_id}
```

Messages and events are delivered to this room.

This ensures correct routing even with multiple connections.

---

## Presence System

The system tracks online users.

Two implementations exist:

### In-memory backend

Used for single-instance deployments.

Tracks socket connections in local memory.

### Redis backend

Used for horizontally scaled deployments.

Redis stores:

```
presence:user:{id}:connections
presence:online_users
```

This ensures presence information is shared across all API instances.

---

## Typing Indicators

Users emit:

```
typing_start
typing_stop
```

The server forwards these events to the recipient room.

---

## Message Delivery Status

Supported statuses:

```
sent
received
delivered
read
```

Flow:

1 sender emits message
2 receiver acknowledges delivery
3 server updates MongoDB
4 sender receives status update

---

# Horizontal Scaling

The system is designed to scale across multiple API instances.

## Redis Socket.IO Adapter

Socket.IO uses:

```
AsyncRedisManager
```

This enables cross-node event fanout.

Example:

```
instance A emits message
↓
Redis pub/sub
↓
instance B delivers message
```

---

## Presence Registry

Presence is stored in Redis.

All instances share the same presence data.

---

## Shared Services

| Component | Purpose |
|--------|--------|
MongoDB | persistent data |
Redis | realtime coordination |
RabbitMQ | background jobs |
S3 / MinIO | voice storage |

---

# Background Workers

RabbitMQ is used for asynchronous jobs.

Current worker:

### Email worker

Responsibilities:

- send verification emails
- future notification jobs

Flow:

```
API
 ↓
RabbitMQ
 ↓
Email Worker
 ↓
Mailhog
```

---

# Security Features

## Rate Limiting

Rate limiting uses the **limits** library with Redis storage.

Protected endpoints include:

- authentication
- voice upload

Example rule:

```
5 requests per minute
```

---

## Anti Enumeration

Authentication endpoints return identical responses for:

- non existing users
- invalid passwords

This prevents user enumeration.

---

## Secure File Uploads

Voice files are uploaded using presigned URLs.

Benefits:

- server does not proxy file data
- files are not public
- limited time access

---

# Database Design

## Users Collection

Fields:

```
_id
email
password_hash
verified
created_at
```

---

## Messages Collection

Fields:

```
_id
sender_id
receiver_id
voice_url
status
created_at
```

---

# API Endpoints

## Auth

```
POST /auth/register
POST /auth/login
POST /auth/verify → returns access + refresh
POST /auth/refresh → rotates refresh token
POST /auth/logout → revokes refresh token
```

---

## Messages

```
POST /messages/upload-url
POST /messages/voice
GET /messages/history
```

---

## Realtime

```
GET /realtime/online-users
GET /realtime/presence
```

---

# Socket Events

## Client → Server

```
send_voice_message
typing_start
typing_stop
voice_message_delivered
voice_message_read
```

---

## Server → Client

```
receive_voice_message
voice_message_status
presence_update
typing_start
typing_stop
```

---

# Deployment

## Services

```
api
mongo
redis
rabbitmq
mailhog
worker
```

---

## Start System

```
docker compose up --build
```

---

# Development

## Run API

```
uvicorn app.main:app --reload
```

---

## Run Worker

```
python -m app.workers.email_worker
```

---

# Testing Realtime

Example:

1 open two clients
2 connect sockets
3 send voice message
4 observe realtime delivery

---

# Scalability Strategy

The system supports horizontal scaling.

Requirements:

- multiple FastAPI replicas
- shared Redis
- shared MongoDB
- shared RabbitMQ

Redis enables cross-instance socket event delivery.

---

# Future Improvements

Potential enhancements:

- push notifications
- message encryption
- voice transcription
- message reactions
- websocket metrics

---

# Summary

This project demonstrates:

- realtime communication
- distributed architecture
- background processing
- secure uploads
- scalable system design

It is designed as a **production-grade backend architecture example** for realtime messaging systems.

