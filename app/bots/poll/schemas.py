from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.db.models import MessageContainerType
from app.db.models.poll import PollResultsVisibility
from app.db.object_id import StrId
from app.modules.messages.schemas import MessageDoc


class PollOptionInput(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=200)


class CreatePollRequest(BaseModel):
    container_type: MessageContainerType
    container_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=500)
    options: list[PollOptionInput] = Field(min_length=2, max_length=10)
    allows_multiple: bool = False
    anonymous: bool = False
    results_visibility: PollResultsVisibility = "after_vote"
    closes_at: datetime | None = None

    @model_validator(mode="after")
    def _unique_option_ids(self) -> "CreatePollRequest":
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("option ids must be unique")
        return self


class PollVoteRequest(BaseModel):
    option_ids: list[str] = Field(min_length=1, max_length=10)


class PollOptionView(BaseModel):
    id: str
    text: str
    # Present only when results are visible to the caller (per results_visibility).
    vote_count: int | None = None


class PollView(BaseModel):
    id: StrId
    container_type: MessageContainerType
    container_id: str
    message_id: StrId
    created_by: StrId
    bot_id: StrId
    question: str
    options: list[PollOptionView]
    allows_multiple: bool
    anonymous: bool
    results_visibility: PollResultsVisibility
    closes_at: datetime | None = None
    closed: bool
    # Aggregate counts, present only when results are visible to the caller.
    total_votes: int | None = None
    results_visible: bool
    # The caller's own current selection (always returned).
    my_option_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreatePollResponse(BaseModel):
    poll: PollView
    message: MessageDoc
