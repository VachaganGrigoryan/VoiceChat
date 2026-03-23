from __future__ import annotations

from io import BytesIO

import pytest

from app.core.errors import AppError
from app.modules.stickers.service import StickersService


def _build_image_bytes(*, size: tuple[int, int], image_format: str) -> bytes:
    pytest.importorskip("PIL")
    from PIL import Image

    image = Image.new("RGBA", size, (255, 0, 0, 255))
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def test_process_sticker_bytes_returns_sanitized_payload():
    service = StickersService(repo=None)  # type: ignore[arg-type]

    result = service._process_sticker_bytes(_build_image_bytes(size=(64, 64), image_format="WEBP"))

    assert result["width"] == 64
    assert result["height"] == 64
    assert len(result["original_bytes"]) > 0
    assert len(result["thumb_bytes"]) > 0
    assert len(result["checksum_sha256"]) == 64


def test_process_sticker_bytes_rejects_non_webp():
    service = StickersService(repo=None)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        service._process_sticker_bytes(_build_image_bytes(size=(64, 64), image_format="PNG"))

    assert exc_info.value.code == "UNSUPPORTED_STICKER_TYPE"


def test_process_sticker_bytes_rejects_large_dimensions():
    service = StickersService(repo=None)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        service._process_sticker_bytes(_build_image_bytes(size=(1024, 1024), image_format="WEBP"))

    assert exc_info.value.code == "STICKER_DIMENSIONS_EXCEEDED"
