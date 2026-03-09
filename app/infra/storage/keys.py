from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4


FolderKind = Literal["avatar", "media", "voice", "audio", "video", "files"]


def _ext(filename: str, fallback: str = ".bin") -> str:
    ext = Path(filename).suffix.lower()
    return ext or fallback


def _build_key(owner_id: str, filename: str) -> str:
    return f"{owner_id}/{uuid4().hex}{_ext(filename)}"


STORAGE_KEY_BUILDERS: dict[FolderKind, Callable[[str, str], str]] = {
    "avatar": lambda owner_id, filename: f"avatars/{_build_key(owner_id, filename)}",
    "media": lambda owner_id, filename: f"media/{_build_key(owner_id, filename)}",
    "voice": lambda owner_id, filename: f"voice/{_build_key(owner_id, filename)}",
    "audio": lambda owner_id, filename: f"audio/{_build_key(owner_id, filename)}",
    "video": lambda owner_id, filename: f"video/{_build_key(owner_id, filename)}",
    "files": lambda owner_id, filename: f"files/{_build_key(owner_id, filename)}",
}


def storage_key_builder(kind: FolderKind) -> Callable[[str, str], str]:
    return STORAGE_KEY_BUILDERS.get(kind, STORAGE_KEY_BUILDERS["files"])