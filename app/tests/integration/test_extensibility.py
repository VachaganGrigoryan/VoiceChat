from __future__ import annotations

import pytest

from app.db.models import UserDocument
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.mark.asyncio
async def test_bot_badge_and_profile(inprocess_client):
    user, tokens = await _create_verified_user_and_tokens("bot-owner@test.com")
    
    # 1. Check default is_bot is False
    assert user.get("is_bot") is not True

    # Get profile and check is_bot is False
    resp = await inprocess_client.get(
        "/users/me",
        headers=_auth(tokens["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["is_bot"] is False

    # 2. Mark user as bot
    await UserDocument.get_pymongo_collection().update_one(
        {"_id": user["_id"]},
        {"$set": {"is_bot": True}}
    )

    # 3. Verify it is now True in response
    resp = await inprocess_client.get(
        "/users/me",
        headers=_auth(tokens["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["is_bot"] is True


@pytest.mark.asyncio
async def test_webhooks_lifecycle(inprocess_client):
    _, tokens = await _create_verified_user_and_tokens("webhook-creator@test.com")

    # 1. Create a webhook
    resp = await inprocess_client.post(
        "/webhooks",
        json={
            "direction": "incoming",
            "target_type": "conversation",
            "target_id": "conv-123",
            "url": "https://example.com/webhook",
            "events": ["message.created"],
        },
        headers=_auth(tokens["access_token"]),
    )
    assert resp.status_code == 201
    webhook_id = resp.json()["data"]["id"]

    # 2. List webhooks
    list_resp = await inprocess_client.get(
        "/webhooks",
        headers=_auth(tokens["access_token"]),
    )
    assert list_resp.status_code == 200
    webhooks = list_resp.json()["data"]
    assert len(webhooks) >= 1
    assert any(w["id"] == webhook_id for w in webhooks)


@pytest.mark.asyncio
async def test_slash_commands_registry(inprocess_client):
    _, tokens = await _create_verified_user_and_tokens("command-creator@test.com")

    # 1. Register a slash command
    resp = await inprocess_client.post(
        "/slash-commands",
        json={
            "trigger": "joke",
            "handler_url": "https://example.com/joke",
            "description": "Get a random joke",
        },
        headers=_auth(tokens["access_token"]),
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["trigger"] == "joke"

    # 2. Duplicate conflict
    resp_conflict = await inprocess_client.post(
        "/slash-commands",
        json={
            "trigger": "joke",
            "handler_url": "https://example.com/another-joke",
            "description": "Duplicate joke command",
        },
        headers=_auth(tokens["access_token"]),
    )
    assert resp_conflict.status_code == 409

    # 3. List slash commands
    list_resp = await inprocess_client.get(
        "/slash-commands",
        headers=_auth(tokens["access_token"]),
    )
    assert list_resp.status_code == 200
    commands = list_resp.json()["data"]
    assert len(commands) >= 1
    assert any(c["trigger"] == "joke" for c in commands)


@pytest.mark.asyncio
async def test_reports_lifecycle(inprocess_client):
    _, reporter_tokens = await _create_verified_user_and_tokens("reporter@test.com")
    _, admin_tokens = await _create_verified_user_and_tokens("moderator@test.com")

    # 1. Submit a report
    resp = await inprocess_client.post(
        "/reports",
        json={
            "target_type": "message",
            "target_id": "msg-123",
            "reason": "Harassment/Spam",
        },
        headers=_auth(reporter_tokens["access_token"]),
    )
    assert resp.status_code == 201
    report_id = resp.json()["data"]["id"]
    assert resp.json()["data"]["status"] == "pending"

    # 2. List reports
    list_resp = await inprocess_client.get(
        "/reports",
        headers=_auth(admin_tokens["access_token"]),
    )
    assert list_resp.status_code == 200
    reports = list_resp.json()["data"]
    assert len(reports) >= 1
    assert any(r["id"] == report_id for r in reports)

    # 3. Resolve report
    resolve_resp = await inprocess_client.patch(
        f"/reports/{report_id}/resolve",
        json={"status": "resolved"},
        headers=_auth(admin_tokens["access_token"]),
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["data"]["status"] == "resolved"


@pytest.mark.asyncio
async def test_audit_logs_appended(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("audit-owner@test.com")
    
    # Create space which triggers the "create_space" audit log
    resp = await inprocess_client.post(
        "/spaces",
        json={
            "name": "Audit Log Space",
            "slug": "audit-log-space",
            "kind": "workspace",
            "visibility": "private",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 201
    space_id = resp.json()["data"]["id"]

    # Verify audit log was appended
    logs_resp = await inprocess_client.get(
        f"/audit-logs?space_id={space_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert logs_resp.status_code == 200
    logs = logs_resp.json()["data"]
    assert len(logs) == 1
    assert logs[0]["action"] == "create_space"
    assert logs[0]["actor_id"] == str(owner["_id"])
    assert logs[0]["space_id"] == space_id
