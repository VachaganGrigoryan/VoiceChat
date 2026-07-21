from __future__ import annotations

from app.db.models import ConversationDocument
from app.db.repository import BaseRepository


class BaseConversationsRepository(BaseRepository[ConversationDocument]):
    model = ConversationDocument
