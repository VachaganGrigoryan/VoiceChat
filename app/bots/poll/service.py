from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId

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
from app.db.models import MessageDocument, PollDocument
from app.db.models.poll import PollOptionDocument
from app.modules.authorization import AuthorizationService
from app.modules.authorization.permissions import MESSAGE_READ, POLL_MANAGE
from app.modules.conversations.service import ConversationsService
from app.modules.messages.service import MessagesService
from app.modules.messages.service.base import SendMessageResult
from app.modules.relationships.repository import RelationshipsRepository



def poll_broadcast_payload(
    poll: PollDocument, *, message: MessageDocument
) -> dict[str, Any]:
    """Non-leaky payload for the `poll_updated` event (shared by the API and the
    auto-close worker).

    Per-viewer tallies depend on `results_visibility`, so the broadcast carries only
    globally-shareable data; clients refetch `GET /polls/{id}` for their
    viewer-correct view. `total_votes` is included only when results are visible to
    everyone (poll closed or `results_visibility == "always"`).
    """
    globally_visible = poll.results_visibility == "always" or poll.closed
    return {
        "container_type": message.container_type,
        "container_id": message.container_id,
        "poll_id": poll.str_id,
        "message_id": str(poll.message_id),
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
        relationships: RelationshipsRepository | None = None,
        authorization: AuthorizationService | None = None,
    ) -> None:
        self.repo = repo
        self.bots_repo = bots_repo
        self.conversations = conversations
        self.messages = messages
        self.relationships = relationships or RelationshipsRepository()
        self.authorization = authorization or AuthorizationService()

    # ---- create -------------------------------------------------------------

    async def create_poll(
        self, *, user_id: str, body: CreatePollRequest
    ) -> PollCreateOutcome:
        conversation = None
        if body.container_type == "conversation":
            conversation = await self.conversations.require_can_create_poll(
                user_id=user_id, conversation_id=body.container_id
            )
        bot = await self.bots_repo.get_by_slug(POLL_BOT.slug)
        if bot is None:
            raise AppError(
                code="POLL_BOT_UNAVAILABLE",
                message="Poll bot is not available",
                status_code=503,
            )

        poll_id = PydanticObjectId()
        send_result = await self.messages.send_poll_ref_message(
            container_type=body.container_type,
            container_id=body.container_id,
            sender_id=str(bot.user_id),
            actor_user_id=user_id,
            poll_id=str(poll_id),
            question=body.question,
        )
        poll = await self.repo.create(
            PollDocument(
                id=poll_id,
                message_id=send_result.message.id,
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

        response = CreatePollResponse(
            poll=self._to_view(poll, message=send_result.message, viewer_id=user_id),
            message=send_result.message,
        )
        return PollCreateOutcome(
            response=response,
            send_result=send_result,
            participant_ids=await self._container_recipient_ids(
                container_type=body.container_type,
                container_id=body.container_id,
                conversation_participant_ids=(
                    [str(pid) for pid in conversation.participant_ids]
                    if conversation is not None
                    else None
                ),
            ),
        )

    # ---- read ---------------------------------------------------------------

    async def get_poll(self, *, user_id: str, poll_id: str) -> PollView:
        poll = await self.repo.get_or_404(poll_id)
        message = await self._require_poll_message(poll=poll, user_id=user_id)
        return self._to_view(poll, message=message, viewer_id=user_id)

    # ---- vote ---------------------------------------------------------------

    async def vote(
        self, *, user_id: str, poll_id: str, option_ids: list[str]
    ) -> PollMutationOutcome:
        poll = await self.repo.get_or_404(poll_id)
        message = await self._require_poll_message(poll=poll, user_id=user_id)
        self._ensure_open(poll)

        selection = self._validate_selection(poll, option_ids)
        poll = await self.repo.upsert_vote(
            poll, user_id=user_id, option_ids=selection
        )
        return await self._mutation_outcome(
            poll, message=message, viewer_id=user_id
        )

    async def retract(
        self, *, user_id: str, poll_id: str
    ) -> PollMutationOutcome:
        poll = await self.repo.get_or_404(poll_id)
        message = await self._require_poll_message(poll=poll, user_id=user_id)
        self._ensure_open(poll)
        poll = await self.repo.remove_vote(poll, user_id=user_id)
        return await self._mutation_outcome(
            poll, message=message, viewer_id=user_id
        )

    # ---- close --------------------------------------------------------------

    async def close(self, *, user_id: str, poll_id: str) -> PollMutationOutcome:
        poll = await self.repo.get_or_404(poll_id)
        message = await self._require_poll_message(poll=poll, user_id=user_id)
        if str(poll.created_by) != user_id:
            allowed = await self.authorization.can(
                user_id,
                POLL_MANAGE,
                message.container_type,
                message.container_id,
            )
            if not allowed:
                raise AppError(
                    code="POLL_CLOSE_FORBIDDEN",
                    message="Only the poll creator or a poll manager can close this poll",
                    status_code=403,
                )
        if not poll.closed:
            poll = await self.repo.mark_closed(poll, closed_by=user_id)
        return await self._mutation_outcome(
            poll, message=message, viewer_id=user_id
        )

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

    def _to_view(
        self, poll: PollDocument, *, message: MessageDocument, viewer_id: str
    ) -> PollView:
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
            container_type=message.container_type,
            container_id=message.container_id,
            message_id=poll.message_id,
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
        self, poll: PollDocument, *, message: MessageDocument, viewer_id: str
    ) -> PollMutationOutcome:
        participant_ids = await self._container_recipient_ids(
            container_type=message.container_type,
            container_id=message.container_id,
        )
        return PollMutationOutcome(
            view=self._to_view(poll, message=message, viewer_id=viewer_id),
            participant_ids=participant_ids,
            broadcast_payload=poll_broadcast_payload(poll, message=message),
        )

    async def _require_poll_message(
        self, *, poll: PollDocument, user_id: str
    ) -> MessageDocument:
        message = await self.messages.repo.get_by_id(message_id=str(poll.message_id))
        if message is None:
            raise AppError(
                code="POLL_MESSAGE_NOT_FOUND",
                message="Poll message not found",
                status_code=404,
            )
        await self.authorization.require(
            user_id,
            MESSAGE_READ,
            message.container_type,
            message.container_id,
            message="Not allowed to access this poll",
        )
        return message

    async def _container_recipient_ids(
        self,
        *,
        container_type: str,
        container_id: str,
        conversation_participant_ids: list[str] | None = None,
    ) -> list[str]:
        if container_type == "conversation":
            if conversation_participant_ids is not None:
                return conversation_participant_ids
            return await self.conversations.conversation_participant_ids(
                conversation_id=container_id
            )

        followers = await self.relationships.list_for_target(
            kind="follow",
            target_type="channel",
            target_id=container_id,
            status="active",
        )
        members = await self.relationships.list_for_target(
            kind="membership",
            target_type="channel",
            target_id=container_id,
            status="active",
        )
        return list(
            dict.fromkeys(
                str(relationship.user_id) for relationship in [*followers, *members]
            )
        )
