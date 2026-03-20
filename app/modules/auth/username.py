from __future__ import annotations

import random
import re
from typing import Final


USERNAME_MIN_LENGTH: Final[int] = 3
USERNAME_MAX_LENGTH: Final[int] = 30

USERNAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])?$"
)

RESERVED_USERNAMES: Final[frozenset[str]] = frozenset(
    {
        "admin",
        "api",
        "app",
        "auth",
        "help",
        "login",
        "logout",
        "me",
        "messages",
        "null",
        "root",
        "settings",
        "support",
        "system",
        "user",
        "users",
        "voice",
    }
)

ADJECTIVES: Final[tuple[str, ...]] = (
    "blue",
    "brave",
    "bright",
    "calm",
    "clever",
    "cool",
    "crimson",
    "frosty",
    "golden",
    "lucky",
    "rapid",
    "silent",
    "silver",
    "swift",
    "wild",
)

NOUNS: Final[tuple[str, ...]] = (
    "aurora",
    "comet",
    "falcon",
    "flame",
    "fox",
    "orbit",
    "panda",
    "phoenix",
    "river",
    "shadow",
    "spark",
    "storm",
    "tiger",
    "wolf",
)


def normalize_username(username: str) -> str:
    return username.strip().lower()


def is_valid_username(username: str) -> bool:
    value = normalize_username(username)

    if value in RESERVED_USERNAMES:
        return False

    if len(value) < USERNAME_MIN_LENGTH or len(value) > USERNAME_MAX_LENGTH:
        return False

    return bool(USERNAME_RE.fullmatch(value))


def generate_username_candidate() -> str:
    adjective = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    suffix = random.randint(10, 9999)
    return f"{adjective}-{noun}-{suffix}"