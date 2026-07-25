from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models import (
    DOCUMENT_MODELS,
    ChannelDocument,
    ConversationDocument,
    OwnerRef,
    SpaceDocument,
)


def test_beanie_registers_channels_collection():
    """Confirm ChannelDocument is in DOCUMENT_MODELS for Beanie init."""
    assert ChannelDocument in DOCUMENT_MODELS


def test_channel_user_owned_valid():
    channel = ChannelDocument(
        owner=OwnerRef(type="user", id="usr_123"),
        slug="my-channel",
        name="My Channel",
        created_by="usr_123",
    )
    assert channel.owner.type == "user"
    assert channel.owner.id == "usr_123"
    assert channel.space_id is None


def test_channel_space_owned_valid():
    channel = ChannelDocument(
        owner=OwnerRef(type="space", id="sp_456"),
        space_id="sp_456",
        slug="general",
        name="General Channel",
        created_by="usr_123",
    )
    assert channel.owner.type == "space"
    assert channel.space_id == "sp_456"


def test_channel_space_owned_missing_space_id_rejected():
    with pytest.raises(ValidationError) as exc:
        ChannelDocument(
            owner=OwnerRef(type="space", id="sp_456"),
            space_id=None,
            slug="general",
            name="General Channel",
            created_by="usr_123",
        )
    assert "space_id" in str(exc.value)


def test_channel_space_owned_mismatched_space_id_rejected():
    with pytest.raises(ValidationError) as exc:
        ChannelDocument(
            owner=OwnerRef(type="space", id="sp_456"),
            space_id="sp_789",
            slug="general",
            name="General Channel",
            created_by="usr_123",
        )
    assert "space_id must match owner.id" in str(exc.value)


def test_channel_profile_kind_requires_user_owner():
    with pytest.raises(ValidationError) as exc:
        ChannelDocument(
            owner=OwnerRef(type="space", id="sp_456"),
            space_id="sp_456",
            kind="profile",
            slug="profile",
            name="Profile",
            created_by="usr_123",
        )
    assert "Profile channel must be owned by a user" in str(exc.value)


def test_channel_unique_slug_index_per_owner():
    """The uniqueness contract is an index, so assert its declared metadata."""
    by_name = {ix.document["name"]: ix.document for ix in ChannelDocument.Settings.indexes}
    unique_slug = by_name["ux_channels_owner_slug"]
    assert unique_slug["unique"] is True
    assert list(unique_slug["key"].keys()) == ["owner.type", "owner.id", "slug"]
    assert "ix_channels_owner" in by_name
    assert "ix_channels_space_id" in by_name
    assert "ix_channels_visibility_kind" in by_name


def test_dm_conversation_invariants():
    dm = ConversationDocument(
        type="dm",
        participant_ids=["usr_1", "usr_2"],
        created_by="usr_1",
    )
    assert dm.type == "dm"
    assert dm.owner is None
    assert dm.space_id is None

    with pytest.raises(ValidationError) as exc:
        ConversationDocument(
            type="dm",
            owner=OwnerRef(type="user", id="usr_1"),
            created_by="usr_1",
        )
    assert "DM conversation cannot have an owner" in str(exc.value)


def test_group_conversation_owner_derivation():
    group_standalone = ConversationDocument(
        type="group",
        participant_ids=["usr_1", "usr_2"],
        created_by="usr_1",
        title="Project X",
    )
    assert group_standalone.owner is not None
    assert group_standalone.owner.type == "user"
    assert group_standalone.owner.id == "usr_1"

    group_space = ConversationDocument(
        type="group",
        participant_ids=["usr_1", "usr_2"],
        created_by="usr_1",
        space_id="sp_100",
        title="Dev Group",
    )
    assert group_space.owner is not None
    assert group_space.owner.type == "space"
    assert group_space.owner.id == "sp_100"


# `thread` is gone entirely (unified-messages): threads are message topology, so
# the type is no longer even readable. `channel` stays until channels re-home.
@pytest.mark.parametrize("legacy_type", ["channel"])
def test_legacy_conversation_types_rejected_on_insert(legacy_type):
    """Blocked below the service layer, so repository writes cannot bypass the contract."""
    conversation = ConversationDocument(
        type=legacy_type,
        participant_ids=["usr_1", "usr_2"],
        created_by="usr_1",
    )
    with pytest.raises(ValueError, match=f"Cannot create a conversation of type '{legacy_type}'"):
        conversation._reject_legacy_types()


def test_legacy_conversation_types_still_readable():
    """Un-migrated rows must keep validating until downstream changes re-home them."""
    legacy = ConversationDocument(
        type="channel",
        participant_ids=["usr_1", "usr_2"],
        created_by="usr_1",
        slug="announcements",
    )
    assert legacy.type == "channel"


@pytest.mark.parametrize(
    "participant_ids",
    [[], ["usr_1"], ["usr_1", "usr_2", "usr_3"], ["usr_1", "usr_1"]],
)
def test_dm_requires_exactly_two_distinct_participants(participant_ids):
    dm = ConversationDocument(
        type="dm",
        participant_ids=participant_ids,
        created_by="usr_1",
    )
    with pytest.raises(ValueError, match="exactly two distinct participants"):
        dm._reject_legacy_types()


def test_dm_with_two_distinct_participants_passes_insert_guard():
    dm = ConversationDocument(
        type="dm",
        participant_ids=["usr_1", "usr_2"],
        created_by="usr_1",
    )
    dm._reject_legacy_types()


def test_group_conversation_passes_insert_guard():
    group = ConversationDocument(
        type="group",
        participant_ids=["usr_1"],
        created_by="usr_1",
        title="Solo for now",
    )
    group._reject_legacy_types()


def test_space_defaults_owner_user_id_from_created_by():
    """Pre-reshape rows carry no owner_user_id; they must still load."""
    space = SpaceDocument(name="Legacy", slug="legacy", created_by="usr_legacy")
    assert space.owner_user_id == "usr_legacy"


def test_space_document_reshaped():
    space = SpaceDocument(
        name="Engineers",
        slug="engineers",
        owner_user_id="usr_admin",
        created_by="usr_admin",
        visibility="public",
        join_policy="open",
        settings={"kind": "community"},
    )
    assert space.owner_user_id == "usr_admin"
    assert space.join_policy == "open"
    assert space.kind == "community"
