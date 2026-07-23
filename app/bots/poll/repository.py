from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import PollDocument, PollVoteDocument
from app.db.repository import BaseRepository


class PollRepository(BaseRepository[PollDocument]):
    model = PollDocument

    async def get_or_404(self, poll_id: str) -> PollDocument:  # type: ignore[override]
        return await super().get_or_404(
            poll_id,
            code="POLL_NOT_FOUND",
            message="Poll not found",
            invalid_message="Invalid poll id",
        )

    async def create(self, poll: PollDocument) -> PollDocument:
        await poll.insert()
        return poll

    async def set_message_id(
        self, poll: PollDocument, *, message_id: str
    ) -> PollDocument:
        poll.message_id = message_id
        await poll.save()
        return poll

    async def upsert_vote(
        self, poll: PollDocument, *, user_id: str, option_ids: list[str]
    ) -> PollDocument:
        """Record (or replace) a user's selection — at most one entry per user."""
        remaining = [vote for vote in poll.votes if str(vote.user_id) != user_id]
        remaining.append(
            PollVoteDocument(
                user_id=user_id,
                option_ids=option_ids,
                voted_at=datetime.now(UTC),
            )
        )
        poll.votes = remaining
        await poll.save()
        return poll

    async def remove_vote(self, poll: PollDocument, *, user_id: str) -> PollDocument:
        poll.votes = [vote for vote in poll.votes if str(vote.user_id) != user_id]
        await poll.save()
        return poll

    async def mark_closed(
        self, poll: PollDocument, *, closed_by: str | None
    ) -> PollDocument:
        poll.closed = True
        poll.closed_at = datetime.now(UTC)
        poll.closed_by = closed_by
        await poll.save()
        return poll

    async def claim_due_for_close(self, *, now: datetime) -> PollDocument | None:
        """Atomically close one open poll whose deadline has passed.

        Race-safe across worker processes: the `closed: False` guard means only one
        claimant wins per poll. Returns the just-closed poll (with votes) so the
        caller can broadcast, or `None` when nothing is due. `$ne: None` excludes
        deadline-less polls (BSON orders `null` below dates, so a bare `$lte` would
        otherwise match them).
        """
        return await self.find_one_and_update(
            {
                "closed": False,
                "closes_at": {"$lte": now, "$ne": None},
            },
            {
                "$set": {
                    "closed": True,
                    "closed_at": now,
                    "closed_by": None,
                    "updated_at": now,
                }
            },
        )
