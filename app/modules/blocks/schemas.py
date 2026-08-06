from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.db.models import BlockDocument
from app.db.object_id import StrId
from app.modules.relationships.schemas import PeerUserSummary


class BlockView(BaseModel):
    id: StrId
    blocker_id: StrId
    blocked_id: StrId
    created_at: datetime
    updated_at: datetime


class BlockedUserListItem(BaseModel):
    block: BlockView
    user: PeerUserSummary


def to_block_view(doc: BlockDocument) -> BlockView:
    return BlockView(
        id=doc.str_id,
        blocker_id=str(doc.blocker_id),
        blocked_id=str(doc.blocked_id),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )
