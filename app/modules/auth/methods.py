from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from app.modules.auth.repository import UsersRepository

AUTH_METHOD_EMAIL = "email"
AUTH_CHALLENGE_MESSAGE = "If the identifier can be used, a verification code has been sent."


@dataclass(slots=True)
class AuthStartContext:
    method: str
    identifier: str
    user_id: str
    message: str


@dataclass(slots=True)
class AuthFinishContext:
    method: str
    identifier: str
    user_id: str
    should_mark_verified: bool


class AuthMethodHandler(Protocol):
    method: str

    async def prepare_start(self, *, identifier: str) -> AuthStartContext: ...

    async def prepare_finish(self, *, identifier: str) -> AuthFinishContext | None: ...

    async def deliver_code(self, *, identifier: str, code: str) -> None: ...

    async def on_success(self, *, finish: AuthFinishContext) -> None: ...


class EmailAuthMethodHandler:
    method = AUTH_METHOD_EMAIL

    def __init__(
        self,
        *,
        users: UsersRepository,
        send_code: Callable[..., Awaitable[None]],
    ) -> None:
        self.users = users
        self.send_code = send_code

    async def prepare_start(self, *, identifier: str) -> AuthStartContext:
        normalized = self._normalize_identifier(identifier)
        user = await self.users.create_if_not_exists(normalized)
        return AuthStartContext(
            method=self.method,
            identifier=normalized,
            user_id=str(user["_id"]),
            message=AUTH_CHALLENGE_MESSAGE,
        )

    async def prepare_finish(self, *, identifier: str) -> AuthFinishContext | None:
        normalized = self._normalize_identifier(identifier)
        user = await self.users.find_by_email(normalized)
        if not user:
            return None

        return AuthFinishContext(
            method=self.method,
            identifier=normalized,
            user_id=str(user["_id"]),
            should_mark_verified=not bool(user.get("is_verified")),
        )

    async def deliver_code(self, *, identifier: str, code: str) -> None:
        await self.send_code(email=self._normalize_identifier(identifier), code=code)

    async def on_success(self, *, finish: AuthFinishContext) -> None:
        if finish.should_mark_verified:
            await self.users.set_verified(finish.user_id)

    def _normalize_identifier(self, identifier: str) -> str:
        return identifier.lower().strip()
