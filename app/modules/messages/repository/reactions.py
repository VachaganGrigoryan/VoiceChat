from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument

from app.core.errors import AppError
from app.db.models import MessageDocument, MessageReactionDocument
from app.db.object_id import parse_object_id as _oid

REACTION_MAX_GROUPS = 10
REACTION_UPDATE_RETRIES = 3


class ReactionsRepositoryMixin:
    async def _replace_reactions_with_retry(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
        remove_only: bool,
    ) -> MessageDocument:
        normalized_emoji = emoji.strip()
        if not normalized_emoji:
            raise AppError(
                code="INVALID_EMOJI", message="emoji is required", status_code=400
            )

        message_oid = _oid(message_id)
        for _ in range(REACTION_UPDATE_RETRIES):
            raw_existing = await self.col.find_one({"_id": message_oid})
            if not raw_existing:
                raise AppError(
                    code="MESSAGE_NOT_FOUND",
                    message="Message not found",
                    status_code=404,
                )
            existing = self._as_message_document(raw_existing)

            self._assert_message_participant(message=existing, user_id=user_id)
            if user_id in existing.hidden_for_user_ids:
                raise AppError(
                    code="MESSAGE_NOT_REACTABLE",
                    message="Hidden messages cannot be reacted to",
                    status_code=400,
                )

            now = datetime.now(UTC)
            reactions: list[MessageReactionDocument] = []
            found_group = False
            changed = False

            for reaction in existing.reactions:
                if reaction.emoji != normalized_emoji:
                    reactions.append(reaction)
                    continue

                found_group = True
                user_ids = [str(uid) for uid in reaction.user_ids]
                has_reaction = user_id in user_ids

                if has_reaction:
                    user_ids = [uid for uid in user_ids if uid != user_id]
                    changed = True
                elif remove_only:
                    user_ids = user_ids
                else:
                    user_ids.append(user_id)
                    changed = True

                if user_ids:
                    reactions.append(
                        MessageReactionDocument(
                            emoji=normalized_emoji,
                            user_ids=user_ids,
                            count=len(user_ids),
                            updated_at=now if changed else reaction.updated_at or now,
                        )
                    )

            if not found_group and not remove_only:
                if len(existing.reactions) >= REACTION_MAX_GROUPS:
                    raise AppError(
                        code="REACTION_LIMIT_EXCEEDED",
                        message="A message can have at most 10 distinct reactions",
                        status_code=400,
                    )
                reactions.append(
                    MessageReactionDocument(
                        emoji=normalized_emoji,
                        user_ids=[user_id],
                        count=1,
                        updated_at=now,
                    )
                )
                changed = True

            update_doc: dict[str, Any] = {
                "reactions": [reaction.model_dump() for reaction in reactions]
            }
            if changed:
                update_doc["updated_at"] = now

            updated = await self.col.find_one_and_update(
                {
                    "_id": existing.id,
                    "updated_at": existing.updated_at,
                },
                {"$set": update_doc},
                return_document=ReturnDocument.AFTER,
            )
            if updated is not None:
                return MessageDocument.model_validate(updated)

        raise AppError(
            code="REACTION_UPDATE_CONFLICT",
            message="Reaction update conflict",
            status_code=409,
        )

    async def add_or_toggle_grouped_reaction(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDocument:
        return await self._replace_reactions_with_retry(
            message_id=message_id,
            user_id=user_id,
            emoji=emoji,
            remove_only=False,
        )

    async def remove_grouped_reaction(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDocument:
        return await self._replace_reactions_with_retry(
            message_id=message_id,
            user_id=user_id,
            emoji=emoji,
            remove_only=True,
        )
