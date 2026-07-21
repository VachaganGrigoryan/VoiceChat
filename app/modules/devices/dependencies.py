from __future__ import annotations

from app.modules.devices.repository import DevicesRepository
from app.modules.devices.service import DevicesService


def get_devices_service() -> DevicesService:
    return DevicesService(repo=DevicesRepository())
