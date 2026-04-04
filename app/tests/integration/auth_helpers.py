from __future__ import annotations

from httpx import AsyncClient, Response


async def start_email_auth(client: AsyncClient, email: str) -> Response:
    return await client.post(
        "/auth/start",
        json={"method": "email", "identifier": email},
    )


async def finish_email_auth(client: AsyncClient, email: str, code: str) -> Response:
    return await client.post(
        "/auth/finish",
        json={"method": "email", "identifier": email, "code": code},
    )
