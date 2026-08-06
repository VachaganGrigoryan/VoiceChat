"""Policy-filtered browse over spaces, channels, groups and people.

Distinct from `/discovery`, which is people-and-privacy shaped, and from
`/search`, which is the message full-text surface. A directory answers "what is
out there that I am allowed to see", cursor-paginated like the rest of the API.

People are delegated to the discovery service rather than reimplemented, so
profile-privacy filtering keeps exactly one implementation.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.models import ChannelDocument, ConversationDocument, SpaceDocument
from app.modules.channels.schemas import ChannelSummary
from app.modules.channels.service import ChannelService
from app.modules.directory.repository import DirectoryRepository
from app.modules.directory.schemas import (
    DirectorySort,
    DirectoryViewerBlock,
    GroupSummary,
    OmniResults,
    SpaceSummary,
)

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
OMNI_PREVIEW = 5

# Which stored field each sort mode orders by, per entity. `relevance` has no
# stored score, so it degrades to the same recency ordering as `recent` — stated
# rather than disguised.
_CHANNEL_SORT_FIELDS: dict[str, str] = {
    "relevance": "follower_count",
    "recent": "last_activity_at",
    "popular": "follower_count",
}


class DirectoryService:
    def __init__(self, *, repo: DirectoryRepository | None = None) -> None:
        self.repo = repo or DirectoryRepository()

    @staticmethod
    def _clamp(limit: int | None) -> int:
        return max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))

    @staticmethod
    def _decode(cursor: str | None) -> dict[str, Any] | None:
        return decode_cursor(cursor) if cursor else None

    @staticmethod
    def _next_cursor(items: Sequence[Any], limit: int, field: str) -> str | None:
        if len(items) < limit:
            return None
        last = items[-1]
        return encode_cursor(v=getattr(last, field, None), id=last.str_id)

    # --- channels -----------------------------------------------------------

    async def list_channels(
        self,
        *,
        viewer_id: str,
        q: str | None = None,
        owner_type: str | None = None,
        space_id: str | None = None,
        kind: str | None = None,
        tags: Sequence[str] | None = None,
        sort: DirectorySort = "relevance",
        cursor: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[ChannelSummary], str | None]:
        capped = self._clamp(limit)
        sort_field = _CHANNEL_SORT_FIELDS[sort]
        channels = await self.repo.list_channels(
            q=q,
            owner_type=owner_type,
            space_id=space_id,
            kind=kind,
            tags=tags,
            blocked_user_ids=await self.repo.blocked_user_ids(viewer_id),
            sort_field=sort_field,
            cursor=self._decode(cursor),
            limit=capped,
        )
        memberships, follows = await self.repo.viewer_edges(
            user_id=viewer_id,
            target_type="channel",
            target_ids=[channel.str_id for channel in channels],
        )
        summaries = [
            self._channel_summary(channel, memberships, follows) for channel in channels
        ]
        return summaries, self._next_cursor(channels, capped, sort_field)

    @staticmethod
    def _channel_summary(
        channel: ChannelDocument,
        memberships: dict[str, str],
        follows: set[str],
    ) -> ChannelSummary:
        summary = ChannelService.to_summary(channel)
        # Reuses the shared viewer block so a directory row and a channel detail
        # describe the viewer the same way.
        from app.modules.channels.schemas import ViewerBlock

        summary.viewer = ViewerBlock(
            membership_status=memberships.get(channel.str_id),
            is_follower=channel.str_id in follows,
        )
        return summary

    # --- spaces -------------------------------------------------------------

    async def list_spaces(
        self,
        *,
        viewer_id: str,
        q: str | None = None,
        join_policy: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[SpaceSummary], str | None]:
        """Public spaces, default space first.

        Note on `sort`: `SpaceDocument` carries no denormalized member counter,
        so `popular` cannot order the query. Ordering is by recency for every
        mode and `member_count` is resolved for the returned page only, for
        display. Faking a popularity order would be worse than not having one.
        """
        capped = self._clamp(limit)
        spaces = await self.repo.list_spaces(
            q=q,
            join_policy=join_policy,
            blocked_user_ids=await self.repo.blocked_user_ids(viewer_id),
            cursor=self._decode(cursor),
            limit=capped,
        )
        space_ids = [space.str_id for space in spaces]
        memberships, _ = await self.repo.viewer_edges(
            user_id=viewer_id, target_type="space", target_ids=space_ids
        )
        counts = await self.repo.count_space_members(space_ids)

        summaries = [
            self._space_summary(space, memberships, counts) for space in spaces
        ]
        # The global space sorts first wherever it appears, mirroring /spaces/me.
        summaries.sort(key=lambda summary: not summary.is_default)
        return summaries, self._next_cursor(spaces, capped, "created_at")

    @staticmethod
    def _space_summary(
        space: SpaceDocument,
        memberships: dict[str, str],
        counts: dict[str, int],
    ) -> SpaceSummary:
        settings = space.settings or {}
        return SpaceSummary(
            id=space.str_id,
            slug=space.slug,
            name=space.name,
            description=settings.get("description"),
            avatar=space.avatar,
            visibility=space.visibility,
            join_policy=space.join_policy,
            kind=space.kind,
            member_count=counts.get(space.str_id, 0),
            is_default=space.slug == "vogi" or bool(settings.get("is_default")),
            created_at=space.created_at,
            viewer=DirectoryViewerBlock(
                membership_status=memberships.get(space.str_id)
            ),
        )

    # --- groups -------------------------------------------------------------

    async def list_groups(
        self,
        *,
        viewer_id: str,
        q: str | None = None,
        space_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[GroupSummary], str | None]:
        """Space-public groups only.

        Without an explicit `space_id`, the search is bounded to spaces the
        caller belongs to — a group is not a public entity the way a channel is.
        """
        capped = self._clamp(limit)
        if space_id is not None:
            space_ids = [str(space_id)]
        else:
            space_ids = await self.repo.member_space_ids(viewer_id)
        if not space_ids:
            return [], None

        groups = await self.repo.list_groups(
            q=q,
            space_ids=space_ids,
            blocked_user_ids=await self.repo.blocked_user_ids(viewer_id),
            cursor=self._decode(cursor),
            limit=capped,
        )
        memberships, _ = await self.repo.viewer_edges(
            user_id=viewer_id,
            target_type="conversation",
            target_ids=[group.str_id for group in groups],
        )
        summaries = [self._group_summary(group, memberships) for group in groups]
        return summaries, self._next_cursor(groups, capped, "created_at")

    @staticmethod
    def _group_summary(
        group: ConversationDocument, memberships: dict[str, str]
    ) -> GroupSummary:
        return GroupSummary(
            id=group.str_id,
            title=group.title,
            slug=group.slug,
            description=group.description,
            image=group.image,
            space_id=str(group.space_id),
            member_count=group.member_count,
            created_at=group.created_at,
            viewer=DirectoryViewerBlock(
                membership_status=memberships.get(group.str_id)
            ),
        )

    # --- omni ---------------------------------------------------------------

    async def omni(
        self, *, viewer_id: str, q: str, types: Sequence[str] | None = None
    ) -> OmniResults:
        wanted = set(types or ["spaces", "channels", "groups"])
        results = OmniResults()
        if "spaces" in wanted:
            results.spaces, _ = await self.list_spaces(
                viewer_id=viewer_id, q=q, limit=OMNI_PREVIEW
            )
        if "channels" in wanted:
            results.channels, _ = await self.list_channels(
                viewer_id=viewer_id, q=q, limit=OMNI_PREVIEW
            )
        if "groups" in wanted:
            results.groups, _ = await self.list_groups(
                viewer_id=viewer_id, q=q, limit=OMNI_PREVIEW
            )
        return results
