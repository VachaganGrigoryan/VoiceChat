from __future__ import annotations

from typing import Any, Callable, Protocol, TypeVar

from app.modules.messages.schemas import ConversationItem, MediaMeta, MessageDoc, StickerMessageRef


class StickerMediaResolverProto(Protocol):
    async def get_message_sticker_media_map(
        self,
        *,
        sticker_ids: list[str],
    ) -> dict[str, dict[str, Any]]: ...


class StickerMediaTargetProto(Protocol):
    type: str
    sticker: StickerMessageRef | None
    media: MediaMeta | None


HydrationItemT = TypeVar("HydrationItemT")


class MessageHydrator:
    def __init__(self, sticker_media_resolver: StickerMediaResolverProto | None):
        self.sticker_media_resolver = sticker_media_resolver

    async def hydrate_message(self, message: MessageDoc) -> MessageDoc:
        hydrated = await self.hydrate_messages([message])
        return hydrated[0]

    async def hydrate_messages(self, messages: list[MessageDoc]) -> list[MessageDoc]:
        return await self._hydrate_items(
            items=messages,
            get_target=lambda message: message,
            apply_media=lambda message, media: message.model_copy(
                update={"media": MediaMeta(**media)},
            ),
        )

    async def hydrate_conversation_items(
        self,
        items: list[ConversationItem],
    ) -> list[ConversationItem]:
        return await self._hydrate_items(
            items=items,
            get_target=lambda item: item.last_message,
            apply_media=lambda item, media: item.model_copy(
                update={
                    "last_message": item.last_message.model_copy(
                        update={"media": MediaMeta(**media)},
                    )
                },
            ),
        )

    async def _hydrate_items(
        self,
        *,
        items: list[HydrationItemT],
        get_target: Callable[[HydrationItemT], StickerMediaTargetProto],
        apply_media: Callable[[HydrationItemT, dict[str, Any]], HydrationItemT],
    ) -> list[HydrationItemT]:
        if not items or self.sticker_media_resolver is None:
            return items

        sticker_ids = self._collect_sticker_ids(items=items, get_target=get_target)
        if not sticker_ids:
            return items

        media_by_sticker_id = await self.sticker_media_resolver.get_message_sticker_media_map(
            sticker_ids=sticker_ids,
        )

        hydrated: list[HydrationItemT] = []
        for item in items:
            target = get_target(item)
            if not self._needs_sticker_media(target):
                hydrated.append(item)
                continue

            media = media_by_sticker_id.get(target.sticker.sticker_id)
            if media is None:
                hydrated.append(item)
                continue

            hydrated.append(apply_media(item, media))

        return hydrated

    def _collect_sticker_ids(
        self,
        *,
        items: list[HydrationItemT],
        get_target: Callable[[HydrationItemT], StickerMediaTargetProto],
    ) -> list[str]:
        sticker_ids: list[str] = []
        seen_sticker_ids: set[str] = set()

        for item in items:
            target = get_target(item)
            if not self._needs_sticker_media(target):
                continue

            sticker_id = target.sticker.sticker_id
            if sticker_id in seen_sticker_ids:
                continue

            seen_sticker_ids.add(sticker_id)
            sticker_ids.append(sticker_id)

        return sticker_ids

    def _needs_sticker_media(self, target: StickerMediaTargetProto) -> bool:
        return target.type == "sticker" and target.sticker is not None and target.media is None
