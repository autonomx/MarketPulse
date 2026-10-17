"""Safe serialization adapters for canonical narrative responses."""
from __future__ import annotations

from typing import Any, Mapping

from .authority import AuthorityClass, BeatKind, BeatPurpose, DeliveryMode
from .contracts import (
    CanonicalNarrativeResponse,
    ClaimAssertion,
    DeliveryMetadata,
    GenerationMetadata,
    NarrativeBlock,
    ValidationIssue,
    ValidationReport,
)


def _claims(row: Mapping[str, Any]) -> tuple[ClaimAssertion, ...]:
    return tuple(
        ClaimAssertion(
            claim_id=str(claim.get("claim_id") or ""),
            text=str(claim.get("text") or ""),
            authority=AuthorityClass(
                str(claim.get("authority") or AuthorityClass.PUBLIC_KNOWLEDGE.value)
            ),
            evidence_refs=tuple(
                str(item) for item in claim.get("evidence_refs") or ()
            ),
            scope=str(claim.get("scope") or "player"),
            subject_id=(
                str(claim.get("subject_id"))
                if claim.get("subject_id") is not None
                else None
            ),
            predicate=str(claim.get("predicate") or ""),
            value=claim.get("value"),
            metadata=dict(claim.get("metadata") or {}),
        )
        for claim in row.get("claims") or ()
        if isinstance(claim, Mapping)
    )


def _integer(
    value: Mapping[str, Any],
    key: str,
    default: int,
) -> int:
    raw = value.get(key)
    return default if raw is None else int(raw)


def canonical_response_from_dict(value: Mapping[str, Any]) -> CanonicalNarrativeResponse:
    blocks = tuple(
        NarrativeBlock(
            block_id=str(row.get("block_id") or ""),
            beat_id=str(row.get("beat_id") or ""),
            sequence=int(row.get("sequence") or 0),
            kind=BeatKind(str(row.get("kind") or BeatKind.NARRATION.value)),
            purpose=BeatPurpose(str(row.get("purpose") or BeatPurpose.DIRECT_ANSWER.value)),
            text=str(row.get("text") or ""),
            speaker_id=str(row.get("speaker_id")) if row.get("speaker_id") is not None else None,
            evidence_refs=tuple(str(item) for item in row.get("evidence_refs") or ()),
            claim_refs=tuple(str(item) for item in row.get("claim_refs") or ()),
            claims=_claims(row),
            metadata=dict(row.get("metadata") or {}),
        )
        for row in value.get("blocks") or ()
        if isinstance(row, Mapping)
    )
    validation_raw = raw_validation if isinstance(raw_validation := value.get("validation"), Mapping) else {}
    issues = tuple(
        ValidationIssue(
            code=str(row.get("code") or "unknown"),
            message=str(row.get("message") or ""),
            block_id=str(row.get("block_id")) if row.get("block_id") is not None else None,
            severity=str(row.get("severity") or "error"),
        )
        for row in validation_raw.get("issues") or ()
        if isinstance(row, Mapping)
    )
    generation_raw = raw_generation if isinstance(raw_generation := value.get("generation"), Mapping) else {}
    delivery_raw = raw_delivery if isinstance(raw_delivery := value.get("delivery"), Mapping) else {}
    return CanonicalNarrativeResponse(
        response_id=str(value.get("response_id") or ""),
        request_id=str(value.get("request_id") or ""),
        turn_id=str(value.get("turn_id") or ""),
        campaign_id=str(value.get("campaign_id") or ""),
        revision=int(value.get("revision") or 1),
        blocks=blocks,
        evidence_used=tuple(str(item) for item in value.get("evidence_used") or ()),
        validation=ValidationReport(
            passed=bool(validation_raw.get("passed")),
            issues=issues,
            repair_history=tuple(str(item) for item in validation_raw.get("repair_history") or ()),
            metadata=dict(validation_raw.get("metadata") or {}),
        ),
        generation=GenerationMetadata(
            source=str(generation_raw.get("source") or "unknown"),
            provider=str(generation_raw.get("provider") or ""),
            model=str(generation_raw.get("model") or ""),
            latency_ms=float(generation_raw.get("latency_ms") or 0.0),
            attempt_count=_integer(generation_raw, "attempt_count", 1),
            evidence_count=_integer(generation_raw, "evidence_count", 0),
            beat_count=_integer(generation_raw, "beat_count", len(blocks)),
            hermes_used=bool(generation_raw.get("hermes_used")),
            metadata=dict(generation_raw.get("metadata") or {}),
        ),
        delivery=DeliveryMetadata(
            mode=DeliveryMode(str(delivery_raw.get("mode") or DeliveryMode.BLOCKING.value)),
            status=str(delivery_raw.get("status") or "complete"),
            delivered_block_ids=tuple(str(item) for item in delivery_raw.get("delivered_block_ids") or ()),
            interruption_reason=(
                str(delivery_raw.get("interruption_reason"))
                if delivery_raw.get("interruption_reason") is not None
                else None
            ),
            metadata=dict(delivery_raw.get("metadata") or {}),
        ),
        content_hash=str(value.get("content_hash") or ""),
        schema_version=str(value.get("schema_version") or "rpg_narrative_response_v1"),
        metadata=dict(value.get("metadata") or {}),
    ).with_content_hash()
