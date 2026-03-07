from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.core.security import decode_token


def extract_token(environ: dict[str, Any], auth: Any) -> str | None:
    if isinstance(auth, dict):
        token = auth.get("token")
        if token:
            return token

    qs = environ.get("QUERY_STRING", "")
    if "token=" in qs:
        for part in qs.split("&"):
            if part.startswith("token="):
                return part.split("=", 1)[1]

    return None


def authenticate_socket(environ: dict[str, Any], auth: Any) -> str:
    token = extract_token(environ, auth)
    if not token:
        raise AppError(code="UNAUTHORIZED", message="Missing socket token", status_code=401)

    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise AppError(code="UNAUTHORIZED", message="Invalid token payload", status_code=401)

    return str(user_id)


async def get_socket_user_id(sio, sid: str) -> str | None:
    session = await sio.get_session(sid)
    if not session:
        return None
    user_id = session.get("user_id")
    return str(user_id) if user_id else None