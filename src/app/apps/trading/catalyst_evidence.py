from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator


CatalystSourceType = Literal["sec", "company", "news", "manual"]
SupplyType = Literal[
    "registered_offering", "atm", "warrants", "convertible",
    "shelf_registration", "resale_registration", "equity_line",
]
SupplyStatus = Literal[
    "active", "terminated", "exhausted", "expired", "redeemed",
    "withdrawn", "unknown",
]


class CatalystSupplyFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    supply_type: SupplyType
    status: SupplyStatus = "unknown"
    resolution_status: Literal["resolved", "unresolved"] = "unresolved"
    source_excerpt: str = ""


class CatalystEvidence(BaseModel):
    """Timestamped immutable evidence; never a naked bullish/bearish flag."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    instrument_id: str
    source_type: CatalystSourceType
    source_locator: str
    published_at: datetime
    captured_at: datetime
    headline: str | None = None
    content: str = Field(min_length=1, max_length=100_000)
    text_hash: str = Field(min_length=64, max_length=64)
    facts: dict[str, Any] = Field(default_factory=dict)
    dilution_flags: tuple[str, ...] = ()
    immutable_fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("published_at", "captured_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("catalyst evidence timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("content")
    @classmethod
    def content_matches_normalized_form(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("catalyst evidence content cannot be empty")
        return normalized


class CatalystShadowClassification(BaseModel):
    """Read-only classifier output. It is not an execution authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classifier_id: str
    classifier_version: str
    catalyst_class: Literal[
        "earnings", "regulatory", "contract_partnership", "financing",
        "corporate_action", "clinical", "legal", "other", "unknown",
    ] = "unknown"
    directional_bias: Literal["positive", "negative", "mixed", "unknown"] = "unknown"
    novelty: Literal["new", "recycled", "unclear"] = "unclear"
    dilution_risk: Literal["none_seen", "possible", "explicit", "unknown"] = "unknown"
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence_ids: tuple[str, ...]
    rationale: str = ""
    shadow_only: Literal[True] = True


class CatalystClassifier(Protocol):
    def classify(self, evidence: tuple[CatalystEvidence, ...]) -> CatalystShadowClassification: ...


_SUPPLY_PATTERNS: tuple[tuple[SupplyType, re.Pattern[str]], ...] = (
    ("registered_offering", re.compile(r"\b(registered direct|public offering|securities offering)\b", re.I)),
    ("atm", re.compile(r"\b(?:at[- ]the[- ]market|ATM (?:offering|program|facility|sales agreement))\b", re.I)),
    ("warrants", re.compile(r"\bwarrants?\b", re.I)),
    ("convertible", re.compile(r"\bconvertible (?:note|notes|debt|preferred|securities)\b", re.I)),
    ("shelf_registration", re.compile(r"\bshelf (?:registration|offering)\b", re.I)),
    ("resale_registration", re.compile(r"\b(?:resale registration|selling stockholders?|resale prospectus)\b", re.I)),
    ("equity_line", re.compile(r"\b(?:equity line|equity purchase agreement)\b", re.I)),
)
_TERMINATED = re.compile(r"\b(?:terminated|cancelled|canceled|ended|no longer available|closed)\b", re.I)
_EXHAUSTED = re.compile(r"\b(?:exhausted|fully utilized|fully used|all outstanding warrants? (?:have been |were )?exercised|paid in full)\b", re.I)
_EXPIRED = re.compile(r"\bexpired\b", re.I)
_REDEEMED = re.compile(r"\bredeemed\b", re.I)
_WITHDRAWN = re.compile(r"\bwithdrawn\b", re.I)
_ACTIVE = re.compile(
    r"\b(?:active|remains? available|outstanding|effective|may sell|may issue|may offer|"
    r"entered into|enters? into|(?:entered|enters?) (?:an? )?(?:at[- ]the[- ]market|ATM)|commenced|launched|currently available)\b",
    re.I,
)


def _status(statement: str) -> tuple[SupplyStatus, str]:
    if _EXHAUSTED.search(statement):
        return "exhausted", "resolved"
    if _WITHDRAWN.search(statement):
        return "withdrawn", "resolved"
    if _REDEEMED.search(statement):
        return "redeemed", "resolved"
    if _EXPIRED.search(statement):
        return "expired", "resolved"
    if _TERMINATED.search(statement):
        return "terminated", "resolved"
    if _ACTIVE.search(statement):
        return "active", "resolved"
    return "unknown", "unresolved"


def supply_facts(text: str) -> tuple[CatalystSupplyFact, ...]:
    output: list[CatalystSupplyFact] = []
    statements = [part.strip() for part in re.split(r"(?<=[.!?;])\s+|\n+", text) if part.strip()]
    for statement in statements:
        for supply_type, pattern in _SUPPLY_PATTERNS:
            if not pattern.search(statement):
                continue
            status, resolution = _status(statement)
            output.append(CatalystSupplyFact(
                supply_type=supply_type,
                status=status,
                resolution_status=cast(Any, resolution),
                source_excerpt=statement[:500],
            ))
    unique: dict[tuple[str, str, str], CatalystSupplyFact] = {}
    for fact in output:
        unique[(fact.supply_type, fact.status, fact.source_excerpt)] = fact
    return tuple(unique.values())


def dilution_flags(text: str) -> tuple[str, ...]:
    """Only resolved active supply states are deterministic hard-veto flags."""
    return tuple(sorted({fact.supply_type for fact in supply_facts(text) if fact.status == "active"}))


def _provided_supply_facts(facts: dict[str, Any]) -> tuple[CatalystSupplyFact, ...] | None:
    raw = facts.get("supply_facts")
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raise ValueError("facts.supply_facts must be a list")
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("facts.supply_facts items must be objects")
        payload = dict(item)
        if payload.get("supply_type") == "warrant":
            payload["supply_type"] = "warrants"
        normalized.append(CatalystSupplyFact.model_validate(payload))
    return tuple(normalized)


def capture_catalyst_evidence(
    *,
    evidence_id: str,
    instrument_id: str,
    source_type: CatalystSourceType,
    source_locator: str,
    published_at: datetime,
    raw_text: str,
    headline: str | None = None,
    captured_at: datetime | None = None,
    facts: dict[str, Any] | None = None,
) -> CatalystEvidence:
    captured = captured_at or datetime.now(timezone.utc)
    normalized = " ".join(raw_text.split())
    if not normalized:
        raise ValueError("catalyst evidence content cannot be empty")
    text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    fact_payload = dict(facts or {})
    typed_supply = _provided_supply_facts(fact_payload)
    if typed_supply is None:
        typed_supply = supply_facts(normalized)
    fact_payload["supply_facts"] = [item.model_dump(mode="json") for item in typed_supply]
    flags = tuple(sorted({item.supply_type for item in typed_supply if item.status == "active"}))
    payload: dict[str, Any] = {
        "evidence_id": evidence_id,
        "instrument_id": instrument_id,
        "source_type": source_type,
        "source_locator": source_locator,
        "published_at": published_at.astimezone(timezone.utc).isoformat(),
        "captured_at": captured.astimezone(timezone.utc).isoformat(),
        "headline": headline,
        "content": normalized,
        "text_hash": text_hash,
        "facts": fact_payload,
        "dilution_flags": flags,
    }
    fingerprint_payload = {**payload, "content": None}
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return CatalystEvidence(**payload, immutable_fingerprint=fingerprint)


def classify_catalyst_shadow(
    evidence: list[CatalystEvidence] | tuple[CatalystEvidence, ...],
    classifier: CatalystClassifier,
) -> CatalystShadowClassification:
    """Invoke an LLM/deterministic classifier strictly as a shadow annotation."""
    frozen = tuple(sorted(evidence, key=lambda item: (item.published_at, item.evidence_id)))
    output = classifier.classify(frozen)
    expected_ids = tuple(item.evidence_id for item in frozen)
    if tuple(sorted(output.evidence_ids)) != tuple(sorted(expected_ids)):
        raise ValueError("catalyst classifier must cite exactly the supplied evidence ids")
    if output.shadow_only is not True:
        raise ValueError("catalyst classifier output must remain shadow-only")
    return output
