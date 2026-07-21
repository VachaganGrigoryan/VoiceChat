from __future__ import annotations


def dm_key_for(user_a: str, user_b: str) -> str:
    """Deterministic DM key: the sorted pair string.

    Reuses the legacy ``sorted("{a}_{b}")`` form so the ``ux_conversations_dm_key``
    unique index guarantees one DM per pair and the value matches ids backfilled
    from historical messages.
    """
    a = str(user_a)
    b = str(user_b)
    return f"{a}_{b}" if a < b else f"{b}_{a}"
