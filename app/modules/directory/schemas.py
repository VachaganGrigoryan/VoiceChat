from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DirectorySort = Literal["relevance", "recent", "popular"]
DirectoryEntity = Literal["spaces", "channels", "groups", "people"]


class DirectoryViewerBlock(BaseModel):
    """What the caller's relationship to a listed entity is.

    Drives the join affordance, which is posture rather than permission: a
    stranger and a pending applicant hold the same (empty) permission set but
    need different buttons.
    """

    membership_status: str | None = None
    is_follower: bool = False


class SpaceSummary(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    avatar: dict[str, Any] | None = None
    visibility: Literal["private", "public"]
    join_policy: Literal["open", "approval", "invite_only", "closed"]
    kind: Literal["workspace", "community"]
    #: Resolved for the returned page only. See `DirectoryService` on why this
    #: cannot currently drive the `popular` sort.
    member_count: int = 0
    #: The global space every user already belongs to; clients suppress its
    #: join affordance rather than offering to join something you are in.
    is_default: bool = False
    created_at: datetime
    viewer: DirectoryViewerBlock = Field(default_factory=DirectoryViewerBlock)


class GroupSummary(BaseModel):
    id: str
    title: str | None = None
    slug: str | None = None
    description: str | None = None
    image: dict[str, Any] | None = None
    space_id: str
    member_count: int = 0
    created_at: datetime
    viewer: DirectoryViewerBlock = Field(default_factory=DirectoryViewerBlock)


class OmniResults(BaseModel):
    """A bounded preview, deliberately unpaginated.

    The typed endpoints are the paginated surface; this exists so one keystroke
    can show a little of everything. Returning a cursor here would invite
    clients to page through a shape that has no stable ordering across types.
    """

    spaces: list[SpaceSummary] = Field(default_factory=list)
    channels: list[Any] = Field(default_factory=list)
    groups: list[GroupSummary] = Field(default_factory=list)
    people: list[Any] = Field(default_factory=list)
