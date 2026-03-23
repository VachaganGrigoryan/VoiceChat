from __future__ import annotations

from io import BytesIO

import pytest
from bson import ObjectId

from app.db.mongo import get_db
from app.tests.integration.test_realtime_socket import _create_verified_user_and_tokens, _insert_active_sticker_pack


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _build_webp_bytes(*, size: tuple[int, int] = (64, 64)) -> bytes:
    pytest.importorskip("PIL")
    from PIL import Image

    image = Image.new("RGBA", size, (255, 0, 0, 255))
    buffer = BytesIO()
    image.save(buffer, format="WEBP")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_sticker_upload_complete_publish_and_resolve(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("stickers-owner@test.com")
    content = _build_webp_bytes()

    pack_resp = await inprocess_client.post(
        "/stickers/packs",
        headers=_auth_headers(owner_tokens["access_token"]),
        json={
            "slug": "funny_cats",
            "title": "Funny Cats",
            "description": "Test pack",
            "tags": ["cats", "funny"],
        },
    )
    assert pack_resp.status_code == 201, pack_resp.text
    pack = pack_resp.json()["data"]

    upload_resp = await inprocess_client.post(
        f"/stickers/packs/{pack['id']}/stickers/upload",
        headers=_auth_headers(owner_tokens["access_token"]),
        json={
            "filename": "party_cat.webp",
            "content_type": "image/webp",
            "expected_size": len(content),
        },
    )
    assert upload_resp.status_code == 201, upload_resp.text
    upload_target = upload_resp.json()["data"]
    assert upload_target["upload_method"] == "PUT"

    content_resp = await inprocess_client.put(
        upload_target["upload_url"],
        headers={
            **_auth_headers(owner_tokens["access_token"]),
            "Content-Type": "image/webp",
        },
        content=content,
    )
    assert content_resp.status_code == 204, content_resp.text

    complete_resp = await inprocess_client.post(
        f"/stickers/uploads/{upload_target['upload_session_id']}/complete",
        headers=_auth_headers(owner_tokens["access_token"]),
        json={
            "slug": "party_cat",
            "title": "Party Cat",
            "emoji_aliases": ["🎉", "😺"],
            "sort_order": 1,
        },
    )
    assert complete_resp.status_code == 201, complete_resp.text
    sticker = complete_resp.json()["data"]
    assert sticker["slug"] == "party_cat"
    assert sticker["pack_slug"] == "funny_cats"
    assert sticker["cdn_url"].startswith("/media/")
    assert sticker["thumb_url"].startswith("/media/")

    stored_sticker = await get_db()["stickers"].find_one({"_id": ObjectId(sticker["id"])})
    assert stored_sticker is not None
    assert stored_sticker["storage"] == "local"

    publish_resp = await inprocess_client.post(
        f"/stickers/packs/{pack['id']}/publish",
        headers=_auth_headers(owner_tokens["access_token"]),
    )
    assert publish_resp.status_code == 200, publish_resp.text
    assert publish_resp.json()["data"]["status"] == "active"

    resolve_resp = await inprocess_client.post(
        "/stickers/resolve",
        headers=_auth_headers(owner_tokens["access_token"]),
        json={"sticker_ids": [sticker["id"]]},
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    resolved = resolve_resp.json()["data"]["items"]
    assert len(resolved) == 1
    assert resolved[0]["sticker_id"] == sticker["id"]
    assert resolved[0]["cdn_url"].startswith("/media/")


@pytest.mark.asyncio
async def test_sticker_upload_rejects_oversized_expected_size(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("stickers-too-big@test.com")

    pack_resp = await inprocess_client.post(
        "/stickers/packs",
        headers=_auth_headers(owner_tokens["access_token"]),
        json={"slug": "oversized_pack", "title": "Oversized Pack"},
    )
    assert pack_resp.status_code == 201, pack_resp.text
    pack = pack_resp.json()["data"]

    upload_resp = await inprocess_client.post(
        f"/stickers/packs/{pack['id']}/stickers/upload",
        headers=_auth_headers(owner_tokens["access_token"]),
        json={
            "filename": "too_big.webp",
            "content_type": "image/webp",
            "expected_size": 512 * 1024 + 1,
        },
    )
    assert upload_resp.status_code == 413, upload_resp.text
    assert upload_resp.json()["error"]["code"] == "STICKER_FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_stickers_search_and_by_ref_are_owner_scoped(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("stickers-search-owner@test.com")
    _, sticker = await _insert_active_sticker_pack(
        owner_user_id=str(owner["_id"]),
        pack_slug="emoji_pack",
        sticker_slug="party_cat",
        emoji_aliases=["🎉", "😺"],
    )

    search_resp = await inprocess_client.get(
        "/stickers/search?emoji=%F0%9F%8E%89",
        headers=_auth_headers(owner_tokens["access_token"]),
    )
    assert search_resp.status_code == 200, search_resp.text
    items = search_resp.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == str(sticker["_id"])

    by_ref_resp = await inprocess_client.get(
        "/stickers/by-ref/emoji_pack/party_cat",
        headers=_auth_headers(owner_tokens["access_token"]),
    )
    assert by_ref_resp.status_code == 200, by_ref_resp.text
    assert by_ref_resp.json()["data"]["id"] == str(sticker["_id"])
