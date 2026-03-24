from __future__ import annotations

from app.modules.webrtc.service import WebRTCService


def get_webrtc_service() -> WebRTCService:
    return WebRTCService()

