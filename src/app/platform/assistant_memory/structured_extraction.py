"""Structured post-turn memory extraction with deterministic validation and fallback."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.conversation.memory_contracts import MemoryCategory, MemoryKind, MemoryScope
from .structured_provider import StructuredProposalProvider
from .typed_memory import validate_typed_payload

ClaimType = Literal[
    "explicit_command",
    "user_asserted",
    "repeated_observation",
    "assistant_inference",
]
ProposalExtractor = Literal["structured_provider_v1", "deterministic_fallback_v1"]

_SECRET_TERMS = (
    "password",
    "passcode",
    "secret",
    "credential",
    "api key",
    "api_key",
    "access token",
    "refresh token",
    "private key",
    "credit card",
    "cvv",
    "social insurance number",
    "social security number",
)
_EXTERNAL_MARKERS = (
    "http://",
    "https://",
    "context retrieved for this turn",
    "ignore previous instructions",
    "ignore previous",
    "system prompt",
    "developer message",
    "retrieved document says",
    "external tool says",
    "web page says",
)
_SENSITIVE_INFERENCE_TERMS = (
    "race",
    "ethnicity",
    "religion",
    "sexual orientation",
    "gender identity",
    "political affiliation",
    "medical diagnosis",
    "mental illness",
    "disability",
    "pregnant",
    "citizenship status",
)
_STOP_TERMS = {
    "a",
    "an",
    "and",
    "around",
    "at",
    "by",
    "for",
    "from",
    "i",
    "is",
    "it",
    "my",
    "of",
    "on",
    "the",
    "to",
    "user",
    "usually",
}
_DEFAULT_CATEGORY: dict[MemoryKind, MemoryCategory] = {
    "semantic_fact": "fact",
    "preference": "preference",
    "instruction": "instruction",
    "relationship_state": "relationship",
    "episode": "fact",
    "routine": "fact",
    "goal": "project",
    "open_loop": "project",
    "temporal_fact": "fact",
    "pronunciation": "preference",
}
_SIMPLE_KINDS: frozenset[MemoryKind] = frozenset(
    {
        "semantic_fact",
        "preference",
        "instruction",
        "relationship_state",
        "pronunciation",
    }
)
_TERM_PATTERN = re.compile(r"[A-Za-z0-9_'-]{2,}")


class StructuredMemoryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: MemoryKind
    claim_type: ClaimType
    scope: MemoryScope = "global"
    category: MemoryCategory
    content: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_message_ids: list[str] = Field(default_factory=list, max_length=64)
    contradiction_key: str | None = Field(default=None, max_length=200)
    extractor: ProposalExtractor = "deterministic_fallback_v1"


class _ProviderProposalCandidate(BaseModel):
    """Untrusted provider shape; ownership and evidence fields are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind
    claim_type: Literal["user_asserted", "assistant_inference"]
    category: MemoryCategory
    content: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    contradiction_key: str | None = Field(default=None, max_length=200)


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _parse_time(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().casefold().replace(" ", "")
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    marker = match.group(3)
    if marker:
        if hour < 1 or hour > 12:
            return None
        if marker == "pm" and hour != 12:
            hour += 12
        elif marker == "am" and hour == 12:
            hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _days(text: str) -> list[str]:
    lowered = text.casefold()
    if "weekday" in lowered or "workday" in lowered:
        return ["MO", "TU", "WE", "TH", "FR"]
    aliases = {
        "monday": "MO",
        "tuesday": "TU",
        "wednesday": "WE",
        "thursday": "TH",
        "friday": "FR",
        "saturday": "SA",
        "sunday": "SU",
    }
    return [value for name, value in aliases.items() if name in lowered]


def _proposal(
    *,
    kind: MemoryKind,
    category: MemoryCategory,
    content: str,
    payload: dict[str, Any],
    confidence: float,
    message_id: str,
    claim_type: ClaimType = "user_asserted",
    contradiction_key: str | None = None,
    extractor: ProposalExtractor = "deterministic_fallback_v1",
) -> StructuredMemoryProposal:
    return StructuredMemoryProposal(
        kind=kind,
        claim_type=claim_type,
        category=category,
        content=_normalize_text(content),
        payload=validate_typed_payload(kind, payload),
        confidence=confidence,
        evidence_message_ids=[message_id],
        contradiction_key=contradiction_key,
        extractor=extractor,
    )


def _input_rejection(text: str) -> str | None:
    lowered = text.casefold()
    if not text:
        return "empty_message"
    if any(marker in lowered for marker in _EXTERNAL_MARKERS):
        return "external_or_instructional_content"
    if any(term in lowered for term in _SECRET_TERMS):
        return "sensitive_content"
    return None


def _terms(value: str) -> frozenset[str]:
    return frozenset(
        term.casefold()
        for term in _TERM_PATTERN.findall(value)
        if term.casefold() not in _STOP_TERMS
    )


def _supported_by_source(source: str, candidate: _ProviderProposalCandidate) -> bool:
    source_terms = _terms(source)
    proposal_terms = _terms(candidate.content)
    activity_terms = _terms(str(candidate.payload.get("activity") or "").replace("_", " "))
    evidence_terms = proposal_terms | activity_terms
    return not evidence_terms or bool(source_terms & evidence_terms)


def _validate_provider_rows(
    rows: list[dict[str, Any]],
    *,
    source: str,
    source_message_id: str,
) -> tuple[list[StructuredMemoryProposal], list[str]]:
    accepted: list[StructuredMemoryProposal] = []
    skipped: list[str] = []
    for raw in rows[:8]:
        if not isinstance(raw, Mapping):
            skipped.append("structured_provider_invalid_schema")
            continue
        try:
            candidate = _ProviderProposalCandidate.model_validate(raw)
        except ValidationError:
            skipped.append("structured_provider_invalid_schema")
            continue
        normalized_content = _normalize_text(candidate.content)
        lowered = normalized_content.casefold()
        if any(marker in lowered for marker in _EXTERNAL_MARKERS) or any(
            term in lowered for term in _SECRET_TERMS
        ):
            skipped.append("structured_provider_sensitive_or_untrusted")
            continue
        if candidate.claim_type == "assistant_inference" and any(
            term in lowered for term in _SENSITIVE_INFERENCE_TERMS
        ):
            skipped.append("structured_provider_sensitive_inference")
            continue
        if candidate.category != _DEFAULT_CATEGORY[candidate.kind]:
            skipped.append("structured_provider_category_mismatch")
            continue
        if candidate.kind in _SIMPLE_KINDS and candidate.payload:
            skipped.append("structured_provider_unsupported_payload")
            continue
        if not _supported_by_source(source, candidate):
            skipped.append("structured_provider_unsupported_claim")
            continue
        try:
            payload = validate_typed_payload(candidate.kind, candidate.payload)
        except (KeyError, TypeError, ValueError, ValidationError):
            skipped.append("structured_provider_invalid_payload")
            continue
        confidence = min(
            candidate.confidence,
            0.99 if candidate.claim_type == "user_asserted" else 0.80,
        )
        accepted.append(
            _proposal(
                kind=candidate.kind,
                category=candidate.category,
                content=normalized_content,
                payload=payload,
                confidence=confidence,
                message_id=source_message_id,
                claim_type=candidate.claim_type,
                contradiction_key=candidate.contradiction_key,
                extractor="structured_provider_v1",
            )
        )
    unique: dict[tuple[str, str], StructuredMemoryProposal] = {}
    for item in accepted:
        unique[(item.kind, item.content.casefold())] = item
    return list(unique.values()), list(dict.fromkeys(skipped))


def _explicit_command(text: str, source_message_id: str) -> StructuredMemoryProposal | None:
    explicit = re.match(r"^(?:remember that|please remember that)\s+(.+)$", text, re.I)
    if explicit is None:
        return None
    return _proposal(
        kind="semantic_fact",
        category="fact",
        content=explicit.group(1).strip(),
        payload={},
        confidence=1.0,
        message_id=source_message_id,
        claim_type="explicit_command",
    )


def _deterministic_fallback(
    text: str,
    *,
    source_message_id: str,
) -> tuple[list[StructuredMemoryProposal], list[str]]:
    proposals: list[StructuredMemoryProposal] = []

    preference = re.match(r"^(?:i\s+(?:really\s+)?prefer|i\s+like)\s+(.+)$", text, re.I)
    if preference:
        proposals.append(
            _proposal(
                kind="preference",
                category="preference",
                content=f"The user prefers {preference.group(1).strip()}",
                payload={},
                confidence=0.95,
                message_id=source_message_id,
            )
        )

    instruction = re.match(r"^(?:always|please\s+always)\s+(.+)$", text, re.I)
    if instruction:
        proposals.append(
            _proposal(
                kind="instruction",
                category="instruction",
                content=instruction.group(1).strip(),
                payload={},
                confidence=0.95,
                message_id=source_message_id,
            )
        )

    routine = re.match(
        r"^i\s+(?:normally|usually|typically|generally)\s+"
        r"(?:(.+?)\s+(?:at|around|by)\s+"
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)(?:\s+(.*))?|(.+))$",
        text,
        re.I,
    )
    if routine:
        activity = (routine.group(1) or routine.group(4)).strip().rstrip(".")
        time_value = _parse_time(routine.group(2))
        payload: dict[str, Any] = {
            "activity": activity.casefold().replace(" ", "_"),
            "days": _days(text),
            "evidence_count": 1,
        }
        if time_value:
            payload["start_time"] = time_value
        proposals.append(
            _proposal(
                kind="routine",
                category="fact",
                content=f"The user usually {activity}",
                payload=payload,
                confidence=0.9,
                message_id=source_message_id,
                contradiction_key=f"routine:{payload['activity']}",
            )
        )

    changed_routine = re.match(r"^i\s+(?:now|no longer)\s+(.+)$", text, re.I)
    if changed_routine:
        activity = changed_routine.group(1).strip().rstrip(".")
        proposals.append(
            _proposal(
                kind="routine",
                category="fact",
                content=f"The user now {activity}",
                payload={
                    "activity": "current_routine",
                    "days": _days(text),
                    "evidence_count": 1,
                },
                confidence=0.85,
                message_id=source_message_id,
                contradiction_key="routine:current_routine",
            )
        )

    goal = re.match(r"^i(?:'m| am)\s+(?:trying|planning|hoping)\s+to\s+(.+)$", text, re.I)
    if goal:
        target = goal.group(1).strip().rstrip(".")
        proposals.append(
            _proposal(
                kind="goal",
                category="project",
                content=f"The user is trying to {target}",
                payload={"state": "active", "priority": 50},
                confidence=0.9,
                message_id=source_message_id,
                contradiction_key=f"goal:{target.casefold()[:120]}",
            )
        )

    open_loop = re.match(r"^i\s+(?:need|have)\s+to\s+(.+)$", text, re.I)
    if open_loop:
        action = open_loop.group(1).strip().rstrip(".")
        proposals.append(
            _proposal(
                kind="open_loop",
                category="project",
                content=f"The user needs to {action}",
                payload={"state": "open"},
                confidence=0.85,
                message_id=source_message_id,
                contradiction_key=f"open-loop:{action.casefold()[:110]}",
            )
        )

    relationship = re.match(r"^(.{1,80}?)\s+is\s+my\s+(.{2,120})$", text, re.I)
    if relationship:
        proposals.append(
            _proposal(
                kind="relationship_state",
                category="relationship",
                content=f"{relationship.group(1).strip()} is the user's {relationship.group(2).strip()}",
                payload={},
                confidence=0.9,
                message_id=source_message_id,
                contradiction_key=f"relationship:{relationship.group(1).casefold().strip()}",
            )
        )

    temporal = re.match(r"^my\s+(.{2,80}?)\s+is\s+(.{2,240})$", text, re.I)
    if temporal:
        proposals.append(
            _proposal(
                kind="semantic_fact",
                category="fact",
                content=f"The user's {temporal.group(1).strip()} is {temporal.group(2).strip()}",
                payload={},
                confidence=0.9,
                message_id=source_message_id,
                contradiction_key=f"fact:{temporal.group(1).casefold().strip()}",
            )
        )

    if not proposals:
        return [], ["no_durable_candidate"]
    unique: dict[tuple[str, str], StructuredMemoryProposal] = {}
    for item in proposals:
        unique[(item.kind, item.content.casefold())] = item
    return list(unique.values()), []


def extract_structured_memory_proposals(
    content: str,
    *,
    source_message_id: str,
    proposal_provider: StructuredProposalProvider | None = None,
) -> tuple[list[StructuredMemoryProposal], list[str]]:
    """Extract provider proposals, validate them, then use a deterministic fallback."""

    text = _normalize_text(content)
    rejection = _input_rejection(text)
    if rejection is not None:
        return [], [rejection]

    explicit = _explicit_command(text, source_message_id)
    if explicit is not None:
        return [explicit], []

    provider_skips: list[str] = []
    if proposal_provider is not None:
        try:
            rows = proposal_provider.propose(text)
            proposals, provider_skips = _validate_provider_rows(
                rows,
                source=text,
                source_message_id=source_message_id,
            )
            if proposals:
                return proposals, provider_skips
            if not provider_skips:
                provider_skips.append("structured_provider_no_durable_candidate")
        except Exception:
            provider_skips.append("structured_provider_failed")

    fallback, fallback_skips = _deterministic_fallback(
        text,
        source_message_id=source_message_id,
    )
    return fallback, list(dict.fromkeys([*provider_skips, *fallback_skips]))


__all__ = [
    "ClaimType",
    "ProposalExtractor",
    "StructuredMemoryProposal",
    "extract_structured_memory_proposals",
]
