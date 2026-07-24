"""Beanie document models, split per-domain.

This package re-exports every Document and embedded model so existing
`from app.db.models import XDocument` imports keep working.
"""

from __future__ import annotations

from beanie import Document

from app.db.models.audit_log import AuditLogDocument
from app.db.models.auth import RefreshTokenDocument
from app.db.models.block import BlockDocument
from app.db.models.bot import BotDocument
from app.db.models.call import CallDocument
from app.db.models.conversation import ConversationDocument
from app.db.models.device import DeviceDocument
from app.db.models.device_prekey import DevicePreKeyDocument
from app.db.models.discovery import DiscoveryTokenDocument
from app.db.models.embedded import (
    CallMessageDocument,
    CallParticipantStateDocument,
    ConversationPreviewDocument,
    EncryptionEnvelopeDocument,
    ForwardedFromDocument,
    MediaDocument,
    MessageContentDocument,
    MessageEditDocument,
    MessageReactionDocument,
    PlaintextContentDocument,
    PollRefDocument,
    ReplyPreviewDocument,
)
from app.db.models.invite_link import InviteLinkDocument
from app.db.models.join_request import JoinRequestDocument
from app.db.models.message import MessageDocument
from app.db.models.message_receipt import MessageReceiptDocument
from app.db.models.notification import NotificationDocument
from app.db.models.participant import ParticipantDocument
from app.db.models.passkey import PasskeyChallengeDocument, PasskeyDocument
from app.db.models.ping import PingDocument
from app.db.models.poll import (
    PollDocument,
    PollOptionDocument,
    PollVoteDocument,
)
from app.db.models.push_token import PushTokenDocument
from app.db.models.report import ReportDocument
from app.db.models.saved_message import SavedMessageDocument
from app.db.models.space import SpaceDocument
from app.db.models.space_member import SpaceMemberDocument
from app.db.models.user import UserDocument
from app.db.models.verification import VerificationCodeDocument
from app.db.models.webhook import WebhookDocument
from app.db.models.slash_command import SlashCommandDocument

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
    MessageReceiptDocument,
    ConversationDocument,
    ParticipantDocument,
    DeviceDocument,
    DevicePreKeyDocument,
    SpaceDocument,
    SpaceMemberDocument,
    InviteLinkDocument,
    JoinRequestDocument,
    BlockDocument,
    PushTokenDocument,
    SavedMessageDocument,
    NotificationDocument,
    BotDocument,
    PollDocument,
    WebhookDocument,
    ReportDocument,
    AuditLogDocument,
    SlashCommandDocument,
]

__all__ = [
    "DOCUMENT_MODELS",
    "AuditLogDocument",
    "BlockDocument",
    "BotDocument",
    "CallDocument",
    "CallMessageDocument",
    "CallParticipantStateDocument",
    "ConversationDocument",
    "ConversationPreviewDocument",
    "DeviceDocument",
    "DevicePreKeyDocument",
    "DiscoveryTokenDocument",
    "EncryptionEnvelopeDocument",
    "ForwardedFromDocument",
    "InviteLinkDocument",
    "JoinRequestDocument",
    "MediaDocument",
    "MessageContentDocument",
    "MessageDocument",
    "MessageEditDocument",
    "MessageReceiptDocument",
    "MessageReactionDocument",
    "NotificationDocument",
    "ParticipantDocument",
    "PasskeyChallengeDocument",
    "PasskeyDocument",
    "PingDocument",
    "PlaintextContentDocument",
    "PollDocument",
    "PollOptionDocument",
    "PollRefDocument",
    "PollVoteDocument",
    "PushTokenDocument",
    "RefreshTokenDocument",
    "ReplyPreviewDocument",
    "ReportDocument",
    "SavedMessageDocument",
    "SpaceDocument",
    "SpaceMemberDocument",
    "UserDocument",
    "VerificationCodeDocument",
    "WebhookDocument",
    "SlashCommandDocument",
]
