"""Beanie document models, split per-domain.

This package re-exports every Document and embedded model so existing
`from app.db.models import XDocument` imports keep working.
"""

from __future__ import annotations

from beanie import Document

from app.db.models.auth import RefreshTokenDocument
from app.db.models.call import CallDocument
from app.db.models.discovery import DiscoveryTokenDocument
from app.db.models.embedded import (
    CallMessageDocument,
    CallParticipantStateDocument,
    MediaDocument,
    MessageReactionDocument,
    ReplyPreviewDocument,
)
from app.db.models.message import MessageDocument
from app.db.models.passkey import PasskeyChallengeDocument, PasskeyDocument
from app.db.models.ping import PingDocument
from app.db.models.user import UserDocument
from app.db.models.verification import VerificationCodeDocument

# Single source of truth for Beanie registration (see app/db/init.py). Order is
# stable but not significant; keep this list in sync when adding a Document.
DOCUMENT_MODELS: list[type[Document]] = [
    VerificationCodeDocument,
    RefreshTokenDocument,
    DiscoveryTokenDocument,
    UserDocument,
    PasskeyDocument,
    PasskeyChallengeDocument,
    PingDocument,
    CallDocument,
    MessageDocument,
]

__all__ = [
    "DOCUMENT_MODELS",
    "CallDocument",
    "CallMessageDocument",
    "CallParticipantStateDocument",
    "DiscoveryTokenDocument",
    "MediaDocument",
    "MessageDocument",
    "MessageReactionDocument",
    "PasskeyChallengeDocument",
    "PasskeyDocument",
    "PingDocument",
    "RefreshTokenDocument",
    "ReplyPreviewDocument",
    "UserDocument",
    "VerificationCodeDocument",
]
