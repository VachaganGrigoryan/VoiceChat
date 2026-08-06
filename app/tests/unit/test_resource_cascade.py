"""The cascade is only correct if it clears every collection that named the parent.

These tests run offline, so they do not exercise Mongo. They assert the thing
that actually regresses: which collections the cascade targets, and in what
order it removes the parent. A forgotten collection is invisible in a happy-path
integration test — the delete still "works" and the orphans surface much later.
"""

from __future__ import annotations

import pytest

from app.db.models import (
    ChannelDocument,
    ConversationDocument,
    SpaceDocument,
)
from app.db.models.embedded import OwnerRef
from app.modules.resources.cascade import ResourceCascade


class _Recorder:
    """Captures which document types the cascade issued deletes against."""

    def __init__(self) -> None:
        self.deleted: list[tuple[str, dict]] = []
        self.parents_deleted: list[str] = []

    @property
    def collections(self) -> set[str]:
        return {name for name, _ in self.deleted}


@pytest.fixture
def cascade(monkeypatch) -> tuple[ResourceCascade, _Recorder]:
    recorder = _Recorder()

    async def fake_delete_many(document, query):
        recorder.deleted.append((document.__name__, query))
        return 0

    async def fake_blob(media):
        return None

    async def fake_roles(*, scope_type, scope_id):
        recorder.deleted.append(("RoleDocument", {"scope_type": scope_type}))
        return 0

    async def no_messages(self, *, container_type, container_id, report):
        # The message sweep is covered separately; here it must not touch Mongo.
        recorder.deleted.append(("MessageDocument", {"container_type": container_type}))
        return []

    monkeypatch.setattr(ResourceCascade, "_delete_many", staticmethod(fake_delete_many))
    monkeypatch.setattr(ResourceCascade, "_delete_blob", staticmethod(fake_blob))
    monkeypatch.setattr(ResourceCascade, "_delete_container_messages", no_messages)

    instance = ResourceCascade()
    monkeypatch.setattr(instance.roles, "delete_for_scope", fake_roles)
    return instance, recorder


def _conversation() -> ConversationDocument:
    doc = ConversationDocument(
        type="group",
        participant_ids=["6890000000000000000000a1"],
        created_by="6890000000000000000000a1",
        owner=OwnerRef(type="user", id="6890000000000000000000a1"),
        title="Design Team",
    )
    doc.id = "6890000000000000000000ff"
    return doc


def _channel() -> ChannelDocument:
    doc = ChannelDocument(
        owner=OwnerRef(type="user", id="6890000000000000000000a1"),
        kind="text",
        slug="general",
        name="General",
        created_by="6890000000000000000000a1",
    )
    doc.id = "6890000000000000000000fe"
    return doc


def _space() -> SpaceDocument:
    doc = SpaceDocument(
        name="Acme",
        slug="acme",
        owner_user_id="6890000000000000000000a1",
        created_by="6890000000000000000000a1",
    )
    doc.id = "6890000000000000000000fd"
    return doc


@pytest.mark.asyncio
async def test_conversation_cascade_clears_every_referencing_collection(
    cascade, monkeypatch
):
    instance, recorder = cascade
    conversation = _conversation()

    async def fake_delete(self):
        recorder.parents_deleted.append("ConversationDocument")

    monkeypatch.setattr(ConversationDocument, "delete", fake_delete)

    await instance.delete_conversation_tree(conversation)

    assert {
        "MessageDocument",
        "MessageReceiptDocument",
        "CallDocument",
        "RelationshipDocument",
        "RoleDocument",
        "InviteLinkDocument",
        "NotificationDocument",
        "WebhookDocument",
        "ReportDocument",
    } <= recorder.collections

    # The parent goes last: an interrupted cascade must leave a resource that
    # still resolves an owner and can simply be deleted again.
    assert recorder.parents_deleted == ["ConversationDocument"]


@pytest.mark.asyncio
async def test_channel_cascade_clears_every_referencing_collection(
    cascade, monkeypatch
):
    instance, recorder = cascade
    channel = _channel()

    async def fake_delete(self):
        recorder.parents_deleted.append("ChannelDocument")

    class _Result:
        modified_count = 0

    async def fake_update_many(*args, **kwargs):
        recorder.deleted.append(("UserDocument.main_channel_id", {}))
        return _Result()

    monkeypatch.setattr(ChannelDocument, "delete", fake_delete)
    from app.db.models import UserDocument

    monkeypatch.setattr(
        UserDocument,
        "get_pymongo_collection",
        classmethod(
            lambda cls: type("C", (), {"update_many": staticmethod(fake_update_many)})()
        ),
    )

    await instance.delete_channel_tree(channel)

    assert {
        "MessageDocument",
        "RelationshipDocument",
        "RoleDocument",
        "InviteLinkDocument",
        "NotificationDocument",
        # A dangling main channel would leave a profile feed pointing at nothing.
        "UserDocument.main_channel_id",
    } <= recorder.collections
    assert recorder.parents_deleted == ["ChannelDocument"]


@pytest.mark.asyncio
async def test_space_cascade_deletes_children_before_itself(cascade, monkeypatch):
    instance, recorder = cascade
    space = _space()
    order: list[str] = []

    async def fake_channel_tree(channel):
        order.append("channel")
        from app.modules.resources.cascade import CascadeReport

        return CascadeReport()

    async def fake_conversation_tree(conversation):
        order.append("group")
        from app.modules.resources.cascade import CascadeReport

        return CascadeReport()

    async def fake_delete(self):
        order.append("space")

    monkeypatch.setattr(instance, "delete_channel_tree", fake_channel_tree)
    monkeypatch.setattr(instance, "delete_conversation_tree", fake_conversation_tree)
    monkeypatch.setattr(SpaceDocument, "delete", fake_delete)

    class _Find:
        def __init__(self, docs):
            self._docs = docs

        async def to_list(self):
            return self._docs

    monkeypatch.setattr(
        ChannelDocument, "find", classmethod(lambda cls, *a, **k: _Find([_channel()]))
    )
    monkeypatch.setattr(
        ConversationDocument,
        "find",
        classmethod(lambda cls, *a, **k: _Find([_conversation()])),
    )

    await instance.delete_space_tree(space)

    # Children first, space last. The reverse strands a child that resolves its
    # owner through the space and could never be managed again.
    assert order == ["channel", "group", "space"]
    assert {
        "RelationshipDocument",
        "RoleDocument",
        "InviteLinkDocument",
    } <= recorder.collections
