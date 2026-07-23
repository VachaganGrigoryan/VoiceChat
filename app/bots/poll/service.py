from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.bots.poll.repository import PollRepository
from app.bots.poll.schemas import (
    CreatePollRequest,
    CreatePollResponse,
    PollOptionView,
    PollView,
)
from app.bots.registry import POLL_BOT
from app.bots.repository import BotsRepository
from app.core.errors import AppError
from app.db.models import PollDocument
from app.db.models.poll import PollOptionDocument
from app.modules.conversations.service import ConversationsService
from app.modules.messages.service import MessagesService
from app.modules.messages.service.base import SendMessageResult

_MANAGER_ROLES = {"owner", "admin"}


def poll_broadcast_payload(poll: PollDocument) -> dict[str, Any]:
    """Non-leaky payload for the `poll_updated` event (shared by the API and the
    auto-close worker).

    Per-viewer tallies depend on `results_visibility`, so the broadcast carries only
    globally-shareable data; clients refetch `GET /polls/{id}` for their
    viewer-correct view. `total_votes` is included only when results are visible to
    everyone (poll closed or `results_visibility == "always"`).
    """
    globally_visible = poll.results_visibility == "always" or poll.closed
    return {
        "conversation_id": str(poll.conversation_id),
        "poll_id": poll.str_id,
        "message_id": str(poll.message_id) if poll.message_id else None,
        "closed": poll.closed,
        "total_votes": len(poll.votes) if globally_visible else None,
        "updated_at": poll.updated_at,
    }


@dataclass
class PollCreateOutcome:
    response: CreatePollResponse
    send_result: SendMessageResult
    participant_ids: list[str]


@dataclass
class PollMutationOutcome:
    view: PollView
    participant_ids: list[str]
    broadcast_payload: dict[str, Any]


class PollService:
    def __init__(
        self,
        *,
        repo: PollRepository,
        bots_repo: BotsRepository,
        conversations: ConversationsService,
        messages: MessagesService,
    ) -> None:
        self.repo = repo
        self.bots_repo = bots_repo
        self.conversations = conversations
        self.messages = messages

    # ---- create -------------------------------------------------------------

    async def create_poll(
        self, *, user_id: str, body: CreatePollRequest
    ) -> PollCreateOutcome:
        conversation = await self.conversations.require_can_create_poll(
            user_id=user_id, conversation_id=body.conversation_id
        )
        bot = await self.bots_repo.get_by_slug(POLL_BOT.slug)
        if bot is None:
            raise AppError(
                code="POLL_BOT_UNAVAILABLE",
                message="Poll bot is not available",
                status_code=503,
            )

        poll = await self.repo.create(
            PollDocument(
                conversation_id=conversation.str_id,
                created_by=user_id,
                bot_id=bot.str_id,
                question=body.question,
                options=[
                    PollOptionDocument(id=option.id, text=option.text)
                    for option in body.options
                ],
                allows_multiple=body.allows_multiple,
                anonymous=body.anonymous,
                results_visibility=body.results_visibility,
                closes_at=body.closes_at,
            )
        )

        send_result = await self.messages.send_poll_ref_message_to_conversation(
            conversation_id=conversation.str_id,
            sender_id=str(bot.user_id),
            poll_id=poll.str_id,
            question=poll.question,
        )
        poll = await self.repo.set_message_id(
            poll, message_id=send_result.message.id
        )

        response = CreatePollResponse(
            poll=self._to_view(poll, viewer_id=user_id),
            message=send_result.message,
        )
        return PollCreateOutcome(
            response=response,
            send_result=send_result,
            participant_ids=[str(pid) for pid in conversation.participant_ids],
        )

    # ---- read ---------------------------------------------------------------

    async def get_poll(self, *, user_id: str, poll_id: str) -> PollView:
        poll = await self.repo.get_or_404(poll_id)
        await self.conversations.require_participant(
            user_id=user_id, conversation_id=str(poll.conversation_id)
        )
        return self._to_view(poll, viewer_id=user_id)

    # ---- vote ---------------------------------------------------------------

    async def vote(
        self, *, user_id: str, poll_id: str, option_ids: list[str]
    ) -> PollMutationOutcome:
        poll = await self.repo.get_or_404(poll_id)
        conversation_id = str(poll.conversation_id)
        await self.conversations.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        self._ensure_open(poll)

        selection = self._validate_selection(poll, option_ids)
        poll = await self.repo.upsert_vote(
            poll, user_id=user_id, option_ids=selection
        )
        return await self._mutation_outcome(poll, viewer_id=user_id)

    async def retract(
        self, *, user_id: str, poll_id: str
    ) -> PollMutationOutcome:
        poll = await self.repo.get_or_404(poll_id)
        conversation_id = str(poll.conversation_id)
        await self.conversations.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        self._ensure_open(poll)
        poll = await self.repo.remove_vote(poll, user_id=user_id)
        return await self._mutation_outcome(poll, viewer_id=user_id)

    # ---- close --------------------------------------------------------------

    async def close(self, *, user_id: str, poll_id: str) -> PollMutationOutcome:
        poll = await self.repo.get_or_404(poll_id)
        conversation_id = str(poll.conversation_id)
        await self.conversations.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        if str(poll.created_by) != user_id:
            role = await self.conversations.get_participant_role(
                user_id=user_id, conversation_id=conversation_id
            )
            if role not in _MANAGER_ROLES:
                raise AppError(
                    code="POLL_CLOSE_FORBIDDEN",
                    message="Only the poll creator or an admin can close this poll",
                    status_code=403,
                )
        if not poll.closed:
            poll = await self.repo.mark_closed(poll, closed_by=user_id)
        return await self._mutation_outcome(poll, viewer_id=user_id)

    # ---- helpers ------------------------------------------------------------

    def _ensure_open(self, poll: PollDocument) -> None:
        now = datetime.now(UTC)
        # Datetimes round-trip through Mongo as offset-naive UTC; make aware before
        # comparing (matches app/modules/messages/service/create.py).
        closes_at = poll.closes_at
        if closes_at is not None and closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=UTC)
        past_deadline = closes_at is not None and now >= closes_at
        if poll.closed or past_deadline:
            raise AppError(
                code="POLL_CLOSED",
                message="This poll is closed",
                status_code=409,
            )

    def _validate_selection(
        self, poll: PollDocument, option_ids: list[str]
    ) -> list[str]:
        valid_ids = {option.id for option in poll.options}
        if any(option_id not in valid_ids for option_id in option_ids):
            raise AppError(
                code="POLL_INVALID_OPTION",
                message="Unknown poll option",
                status_code=400,
            )
        if not poll.allows_multiple:
            if len(option_ids) != 1:
                raise AppError(
                    code="POLL_SINGLE_CHOICE",
                    message="This poll accepts a single choice",
                    status_code=400,
                )
            return option_ids
        # Multiple choice: dedupe while preserving order.
        return list(dict.fromkeys(option_ids))

    def _results_visible(self, poll: PollDocument, *, viewer_voted: bool) -> bool:
        if poll.closed:
            return True
        if poll.results_visibility == "always":
            return True
        if poll.results_visibility == "after_vote":
            return viewer_voted
        return False  # after_close, still open

    def _to_view(self, poll: PollDocument, *, viewer_id: str) -> PollView:
        my_vote = next(
            (vote for vote in poll.votes if str(vote.user_id) == viewer_id), None
        )
        viewer_voted = my_vote is not None
        results_visible = self._results_visible(poll, viewer_voted=viewer_voted)

        counts: dict[str, int] = {}
        if results_visible:
            for vote in poll.votes:
                for option_id in vote.option_ids:
                    counts[option_id] = counts.get(option_id, 0) + 1

        options = [
            PollOptionView(
                id=option.id,
                text=option.text,
                vote_count=counts.get(option.id, 0) if results_visible else None,
            )
            for option in poll.options
        ]
        return PollView(
            id=poll.str_id,
            conversation_id=str(poll.conversation_id),
            message_id=str(poll.message_id) if poll.message_id else None,
            created_by=str(poll.created_by),
            bot_id=str(poll.bot_id),
            question=poll.question,
            options=options,
            allows_multiple=poll.allows_multiple,
            anonymous=poll.anonymous,
            results_visibility=poll.results_visibility,
            closes_at=poll.closes_at,
            closed=poll.closed,
            total_votes=len(poll.votes) if results_visible else None,
            results_visible=results_visible,
            my_option_ids=list(my_vote.option_ids) if my_vote else [],
            created_at=poll.created_at,
            updated_at=poll.updated_at,
        )

    async def _mutation_outcome(
        self, poll: PollDocument, *, viewer_id: str
    ) -> PollMutationOutcome:
        participant_ids = await self.conversations.conversation_participant_ids(
            conversation_id=str(poll.conversation_id)
        )
        return PollMutationOutcome(
            view=self._to_view(poll, viewer_id=viewer_id),
            participant_ids=participant_ids,
            broadcast_payload=poll_broadcast_payload(poll),
        )
