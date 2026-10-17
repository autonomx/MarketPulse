"""Privacy decisions based only on the conversation contract."""
from __future__ import annotations

from typing import Any


def private_session(session: Any) -> bool:
    """Return whether a session is explicitly configured as non-persistent."""
    return str(getattr(session, "transcript_policy", "persistent") or "persistent") != "persistent"


def automatic_memory_derivation_allowed(session: Any) -> bool:
    """Private sessions never create automatic durable memory derivatives."""
    return not private_session(session)


__all__ = ["automatic_memory_derivation_allowed", "private_session"]
