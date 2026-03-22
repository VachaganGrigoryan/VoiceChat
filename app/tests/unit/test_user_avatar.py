from __future__ import annotations

from app.modules.users.avatar import build_user_avatar_payload


def test_build_user_avatar_payload_overrides_stale_url():
    avatar = {
        "storage": "local",
        "key": "avatar/user-1.png",
        "url": "https://stale.example.com/old.png",
        "mime": "image/png",
        "size_bytes": 123,
    }

    result = build_user_avatar_payload(avatar)

    assert result == {
        "storage": "local",
        "key": "avatar/user-1.png",
        "url": "/media/avatar/user-1.png",
        "mime": "image/png",
        "size_bytes": 123,
    }
    assert avatar["url"] == "https://stale.example.com/old.png"


def test_build_user_avatar_payload_returns_none_for_none():
    assert build_user_avatar_payload(None) is None
