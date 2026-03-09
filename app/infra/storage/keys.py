from __future__ import annotations

from pathlib import Path
from uuid import uuid4


def _ext(filename: str, fallback: str = ".bin") -> str:
    ext = Path(filename).suffix.lower()
    return ext or fallback


def build_avatar_key(user_id: str, filename: str) -> str:
    return f"avatars/{user_id}/{uuid4().hex}{_ext(filename)}"


def build_voice_key(sender_id: str, filename: str) -> str:
    return f"voice/{sender_id}/{uuid4().hex}{_ext(filename)}"


def build_image_key(sender_id: str, filename: str) -> str:
    return f"images/{sender_id}/{uuid4().hex}{_ext(filename)}"