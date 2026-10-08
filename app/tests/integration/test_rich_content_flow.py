from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _dm(
    client: AsyncClient, sender_email: str, receiver_email: str
) -> tuple[dict[str, str], dict[str, str], str]:
    sender, sender_tokens = await _create_verified_user_and_tokens(sender_email)
    receiver, receiver_tokens = await _create_verified_user_and_tokens(receiver_email)
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))
    conversation = await client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert conversation.status_code == 200, conversation.text
    return sender_tokens, receiver_tokens, conversation.json()["data"]["id"]


def _attachment(key: str, *, kind: str = "image") -> dict[str, object]:
    mime = "audio/webm" if kind == "voice" else "image/png"
    return {
        "kind": kind,
        "storage": "local",
        "key": key,
        "mime": mime,
        "size_bytes": 1234,
        "duration_ms": 1500 if kind == "voice" else None,
    }


@pytest.mark.asyncio
async def test_rich_content_types_round_trip(inprocess_client):
    sender_tokens, receiver_tokens, conversation_id = await _dm(
        inprocess_client,
        "rich-a@test.com",
        "rich-b@test.com",
    )
    # Polls are no longer a rich-content type; they are created via /polls (see
    # test_poll_bot_flow.py) and link a message to a first-class poll entity.
    cases = [
        (
            "sticker",
            {"sticker": {"url": "https://cdn.test/sticker.webp", "emoji": "ok"}},
        ),
        (
            "voice",
            {
                "text": "voice note",
                "attachments": [_attachment("voice/one.webm", kind="voice")],
            },
        ),
        (
            "location",
            {
                "location": {
                    "latitude": 40.1772,
                    "longitude": 44.5035,
                    "name": "Yerevan",
                }
            },
        ),
        (
            "contact",
            {"contact": {"display_name": "Ada Lovelace", "email": "ada@example.test"}},
        ),
        (
            "link_preview",
            {
                "link_preview": {
                    "url": "https://example.test",
                    "title": "Example",
                    "description": "Preview",
                }
            },
        ),
    ]

    sent_ids: list[str] = []
    for content_type, payload in cases:
        response = await inprocess_client.post(
            f"/messages/conversation/{conversation_id}/content",
            json={"type": content_type, **payload},
            headers=_auth(sender_tokens["access_token"]),
        )
        assert response.status_code == 201, response.text
        message = response.json()["data"]
        sent_ids.append(message["id"])
        assert message["type"] == content_type
        assert message["content"]["type"] == content_type

    listing = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert listing.status_code == 200, listing.text
    listed = {item["id"]: item for item in listing.json()["data"]}
    for message_id in sent_ids:
        assert listed[message_id]["content"]["encryption"] == "none"


@pytest.mark.asyncio
async def test_multi_attachment_order_is_preserved(inprocess_client):
    sender_tokens, receiver_tokens, conversation_id = await _dm(
        inprocess_client,
        "rich-order-a@test.com",
        "rich-order-b@test.com",
    )
    attachments = [_attachment("media/first.png"), _attachment("media/second.png")]

    response = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/content",
        json={
            "type": "link_preview",
            "link_preview": {"url": "https://example.test/album", "title": "Album"},
            "attachments": attachments,
        },
        headers=_auth(sender_tokens["access_token"]),
    )
    assert response.status_code == 201, response.text
    message_id = response.json()["data"]["id"]

    listing = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert listing.status_code == 200, listing.text
    message = next(item for item in listing.json()["data"] if item["id"] == message_id)
    assert [item["key"] for item in message["content"]["attachments"]] == [
        "media/first.png",
        "media/second.png",
    ]


@pytest.mark.asyncio
async def test_unknown_rich_content_type_is_rejected_at_api_boundary(inprocess_client):
    sender_tokens, _receiver_tokens, conversation_id = await _dm(
        inprocess_client,
        "rich-unknown-a@test.com",
        "rich-unknown-b@test.com",
    )

    response = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/content",
        json={"type": "whiteboard", "text": "future payload"},
        headers=_auth(sender_tokens["access_token"]),
    )

    assert response.status_code == 422, response.text
