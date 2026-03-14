from __future__ import annotations

import hashlib
import secrets
import string


CODE_ALPHABET = string.ascii_uppercase + string.digits


def generate_code_token(length: int = 6) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def generate_link_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def preview_token(raw_token: str, keep: int = 4) -> str:
    if len(raw_token) <= keep:
        return raw_token
    return f"{raw_token[:keep]}***"