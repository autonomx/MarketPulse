"""Classification helpers for non-mutating hypothetical turn requests."""
from __future__ import annotations

import re

HYPOTHETICAL_INTENT = "hypothetical_counterfactual"


def looks_like_hypothetical_input(player_input: str) -> bool:
    text = _norm(player_input)
    if not text:
        return False
    return bool(
        re.search(r"\b(if|suppose|what if|imagine)\b", text)
        or re.search(r"\bwould you\b", text)
        or re.search(r"\bif i became\b", text)
        or re.search(r"\bif .* attacked\b", text)
    )


def _norm(value: str) -> str:
    return str(value or "").casefold().strip()


__all__ = ["HYPOTHETICAL_INTENT", "looks_like_hypothetical_input"]
