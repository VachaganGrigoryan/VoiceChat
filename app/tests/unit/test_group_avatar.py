from __future__ import annotations

from app.modules.conversations.group_avatar import build_group_avatar_payload


def test_build_group_avatar_payload_overrides_stale_url():
    image = {
        "storage": "local",
        "key": "avatars/group-1/pic.png",
        "url": "https://stale.example.com/old.png",
        "mime": "image/png",
        "size_bytes": 456,
    }

    result = build_group_avatar_payload(image)

    assert result == {
        "storage": "local",
        "key": "avatars/group-1/pic.png",
        "url": "/media/avatars/group-1/pic.png",
        "mime": "image/png",
        "size_bytes": 456,
    }
    assert image["url"] == "https://stale.example.com/old.png"


def test_build_group_avatar_payload_returns_none_for_none():
    assert build_group_avatar_payload(None) is None
