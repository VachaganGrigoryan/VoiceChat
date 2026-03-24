from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.modules.calls.schemas import IceServer


class IceServersPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ice_servers: list[IceServer] = Field(
        default_factory=list,
        alias="iceServers",
        serialization_alias="iceServers",
    )

