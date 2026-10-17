"""High-precision user-explicit evidence for Companion Activity.

This module deliberately does not attempt general intent classification.  It only recognizes
small explicit linguistic forms whose semantics are clear enough to become user-authoritative
activity evidence.  Ambiguous chat remains ordinary conversation and produces no proposition.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

from .contracts import EvidenceProposition

_OBJECTIVE_PATTERNS = (
    re.compile(r"\b(?:i(?:'m| am) trying to|my goal is to|my goal is)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bthe goal is to\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
)
_STRATEGY_PATTERNS = (
    re.compile(r"\b(?:i(?:'ll| will) try)\s+(.+?)(?:\s+next)?(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\b(?:i(?:'m| am) switching to|my strategy is)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
)
_BLOCKER_PATTERNS = (
    re.compile(r"\b(?:i(?:'m| am) stuck on|the blocker is|what(?:'s| is) blocking me is)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
)
_OPEN_LOOP_PATTERN = re.compile(
    r"\b((?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+more\s+"
    r"(?:tries|attempts|runs|rounds|steps)\b.+?(?:then|before)\b.+?)(?:[.!?]|$)",
    re.IGNORECASE,
)


def user_activity_propositions(
    *,
    session_id: str,
    subject: str,
    message_id: str,
    content: str,
    observed_at: datetime,
) -> tuple[EvidenceProposition, ...]:
    """Return only explicit user-authored activity claims from one accepted chat turn."""

    text = " ".join(str(content or "").split()).strip()
    if not text:
        return ()

    propositions: list[EvidenceProposition] = []
    objective = _first_capture(_OBJECTIVE_PATTERNS, text)
    if objective:
        propositions.append(
            _proposition(
                session_id=session_id,
                subject=subject,
                message_id=message_id,
                predicate="current_objective",
                value=objective,
                observed_at=observed_at,
            )
        )

    strategy = _first_capture(_STRATEGY_PATTERNS, text)
    if strategy:
        propositions.append(
            _proposition(
                session_id=session_id,
                subject=subject,
                message_id=message_id,
                predicate="strategy",
                value=strategy,
                observed_at=observed_at,
            )
        )

    blocker = _first_capture(_BLOCKER_PATTERNS, text)
    if blocker:
        propositions.append(
            _proposition(
                session_id=session_id,
                subject=subject,
                message_id=message_id,
                predicate="blocker",
                value={"description": blocker},
                observed_at=observed_at,
            )
        )

    loop_match = _OPEN_LOOP_PATTERN.search(text)
    if loop_match:
        description = _clean(loop_match.group(1))
        if description:
            loop_id = _stable_loop_id(session_id, description)
            propositions.append(
                _proposition(
                    session_id=session_id,
                    subject=subject,
                    message_id=message_id,
                    predicate="open_loop",
                    value={
                        "loop_id": loop_id,
                        "kind": "bounded_commitment",
                        "description": description,
                        "importance": 0.8,
                        "blocking": False,
                    },
                    observed_at=observed_at,
                )
            )

    return tuple(propositions)


def _proposition(
    *,
    session_id: str,
    subject: str,
    message_id: str,
    predicate: str,
    value: object,
    observed_at: datetime,
) -> EvidenceProposition:
    return EvidenceProposition(
        proposition_id=f"user-activity:{message_id}:{predicate}"[:240],
        subject=subject,
        predicate=predicate,
        value=value,
        source_kind="user",
        trust_level="user_explicit",
        confidence=1.0,
        sensitivity="normal",
        observed_at=observed_at,
        valid_from=observed_at,
        generation=None,
        schema_version="companion-user-activity-evidence@1",
        links=(),
    )


def _first_capture(patterns: tuple[re.Pattern[str], ...], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = _clean(match.group(1))
            if value:
                return value
    return ""


def _clean(value: str) -> str:
    return " ".join(value.strip(" \t\r\n,;:-").split())[:1000]


def _stable_loop_id(session_id: str, description: str) -> str:
    material = f"{session_id}\x1f{description.casefold()}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"user-loop:{digest}"


__all__ = ["user_activity_propositions"]
