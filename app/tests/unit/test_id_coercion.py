from __future__ import annotations

from datetime import UTC, datetime

from beanie import PydanticObjectId

from app.db.models import CallDocument, UserDocument, VerificationCodeDocument
from app.modules.calls.schemas import CallDoc
from app.modules.pings.schemas import PingResponse
from app.modules.users.schemas import UserProfileResponse
from app.modules.users.service import _doc_value


def _now() -> datetime:
    return datetime.now(UTC)


def test_verification_user_id_coerces_objectid_to_str() -> None:
    # Reproduces the reported crash: a PydanticObjectId passed into a str id field.
    oid = PydanticObjectId()
    doc = VerificationCodeDocument(
        method="email",
        identifier="a@b.com",
        user_id=oid,
        purpose="auth",
        code_hash="x",
        expires_at=_now(),
        created_at=_now(),
    )
    assert isinstance(doc.user_id, str)
    assert doc.user_id == str(oid)


def test_call_foreign_ids_coerce_to_str() -> None:
    a, b = PydanticObjectId(), PydanticObjectId()
    doc = CallDocument(
        caller_user_id=a,
        callee_user_id=b,
        participant_user_ids=[a, b],
        type="audio",
        status="ringing",
        room_id="call:x",
        created_at=_now(),
        updated_at=_now(),
    )
    assert isinstance(doc.caller_user_id, str)
    assert all(isinstance(p, str) for p in doc.participant_user_ids)
    assert doc.participant_user_ids == [str(a), str(b)]


def test_str_id_property_stringifies_and_handles_unsaved() -> None:
    unsaved = UserDocument(
        email="a@b.com", username="ab", created_at=_now(), updated_at=_now()
    )
    assert unsaved.id is None
    assert unsaved.str_id == ""  # NOT the old `.id or ""` trap

    saved = UserDocument.model_validate(
        {
            "_id": PydanticObjectId(),
            "email": "c@d.com",
            "username": "cd",
            "created_at": _now(),
            "updated_at": _now(),
        }
    )
    assert isinstance(saved.str_id, str)
    assert saved.str_id == str(saved.id)


def test_api_response_models_coerce_objectids_to_str() -> None:
    user_id = PydanticObjectId()
    peer_id = PydanticObjectId()
    ping_id = PydanticObjectId()
    call_id = PydanticObjectId()

    profile = UserProfileResponse(
        id=user_id,
        email="user@example.com",
        is_verified=True,
        username="user",
        created_at=_now(),
        updated_at=_now(),
        is_private=False,
        default_discovery_enabled=True,
    )
    ping = PingResponse(
        id=ping_id,
        from_user_id=user_id,
        to_user_id=peer_id,
        status="pending",
        created_at=_now(),
        updated_at=_now(),
    )
    call = CallDoc(
        id=call_id,
        caller_user_id=user_id,
        callee_user_id=peer_id,
        participant_user_ids=[user_id, peer_id],
        type="audio",
        status="ringing",
        room_id="call:x",
        created_at=_now(),
        updated_at=_now(),
    )

    assert profile.id == str(user_id)
    assert ping.id == str(ping_id)
    assert ping.from_user_id == str(user_id)
    assert ping.to_user_id == str(peer_id)
    assert call.id == str(call_id)
    assert call.participant_user_ids == [str(user_id), str(peer_id)]


def test_doc_value_stringifies_document_id() -> None:
    user = UserDocument.model_validate(
        {
            "_id": PydanticObjectId(),
            "email": "doc@example.com",
            "username": "docuser",
            "created_at": _now(),
            "updated_at": _now(),
        }
    )

    assert _doc_value(user, "id") == str(user.id)
