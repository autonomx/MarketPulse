"""Semantic claim inference and support validation for canonical narrative blocks."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping, Sequence

from .authority import AuthorityClass, BeatPurpose
from .contracts import (
    ClaimAssertion,
    EvidenceRecord,
    NarrativeBlock,
    TurnPresentationRequest,
    ValidationIssue,
)
from .planner import NarrativePlan

_TOKEN_PATTERN = re.compile(r"[a-z0-9']+", re.IGNORECASE)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for",
    "from", "had", "has", "have", "he", "her", "his", "i", "in", "is", "it",
    "its", "of", "on", "or", "she", "that", "the", "their", "them", "they",
    "this", "to", "was", "were", "will", "with", "you", "your",
}
_FACTUAL_PURPOSES = {
    BeatPurpose.SCENE_ESTABLISHMENT,
    BeatPurpose.ENVIRONMENTAL_CHANGE,
    BeatPurpose.DIRECT_ANSWER,
    BeatPurpose.LORE_REVEAL,
    BeatPurpose.RESOLVED_ACTION,
    BeatPurpose.CONSEQUENCE,
}
_STATE_PREFIXES = (
    "currency", "inventory", "health", "location", "combat", "quest",
    "relationship", "time", "weather",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def claim_ledger(request: TurnPresentationRequest) -> Mapping[str, Mapping[str, Any]]:
    raw = request.authoritative_outcome.get("claim_ledger") or request.metadata.get("claim_ledger") or {}
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if isinstance(value, Mapping)
    }


def _tokens(value: str) -> set[str]:
    return {
        token for token in _TOKEN_PATTERN.findall(value.casefold())
        if token not in _STOPWORDS and len(token) > 1
    }


def _authority(value: Any, default: AuthorityClass) -> AuthorityClass:
    try:
        return AuthorityClass(str(value))
    except (TypeError, ValueError):
        return default


def _first_authority(
    refs: Sequence[str],
    evidence_by_id: Mapping[str, EvidenceRecord],
    default: AuthorityClass,
) -> AuthorityClass:
    for ref in refs:
        record = evidence_by_id.get(ref)
        if record is not None:
            return record.authority
    return default


def infer_claims(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
    blocks: Sequence[NarrativeBlock],
) -> tuple[NarrativeBlock, ...]:
    """Attach deterministic claim ledgers when a writer omitted explicit claims."""

    beats = {beat.beat_id: beat for beat in plan.beats}
    evidence_by_id = {record.evidence_id: record for record in evidence}
    ledger = claim_ledger(request)
    inferred: list[NarrativeBlock] = []
    for block in blocks:
        if block.claims or block.purpose not in _FACTUAL_PURPOSES:
            inferred.append(block)
            continue
        beat = beats.get(block.beat_id)
        scope = str((beat.metadata if beat else {}).get("evidence_scope") or "player")
        claim_ids = tuple(block.claim_refs) or (
            (f"claim:{block.block_id}",) if block.evidence_refs else ()
        )
        repaired_provider_claims = block.metadata.get("provider_claim_repair") is True
        claim_source = "provider_repaired" if repaired_provider_claims else "inferred"
        claims: list[ClaimAssertion] = []
        for claim_id in claim_ids:
            expected = ledger.get(claim_id, {})
            default_authority = (
                AuthorityClass.CONFIRMED_TURN
                if claim_id.startswith(_STATE_PREFIXES)
                else _first_authority(
                    block.evidence_refs,
                    evidence_by_id,
                    AuthorityClass.PUBLIC_KNOWLEDGE,
                )
            )
            claims.append(
                ClaimAssertion(
                    claim_id=claim_id,
                    text=str(expected.get("text") or block.text).strip(),
                    authority=_authority(expected.get("authority"), default_authority),
                    evidence_refs=tuple(block.evidence_refs),
                    scope=str(expected.get("scope") or scope),
                    subject_id=(
                        str(expected.get("subject_id"))
                        if expected.get("subject_id") is not None
                        else block.speaker_id
                    ),
                    predicate=str(expected.get("predicate") or ""),
                    value=expected.get("value"),
                    metadata={"claim_source": claim_source, "ledger_backed": bool(expected)},
                )
            )
        inferred.append(
            replace(
                block,
                claims=tuple(claims),
                metadata={
                    **dict(block.metadata),
                    "claim_source": claim_source if claims else "none",
                },
            )
        )
    return tuple(inferred)


def _authority_supported(
    authority: AuthorityClass,
    supporting: Sequence[EvidenceRecord],
) -> bool:
    actual = {record.authority for record in supporting}
    if not actual:
        return False
    if authority is AuthorityClass.CONFIRMED_TURN:
        return AuthorityClass.CONFIRMED_TURN in actual
    if authority is AuthorityClass.OBJECTIVE_CANON:
        return bool(actual.intersection({
            AuthorityClass.CONFIRMED_TURN,
            AuthorityClass.SCENE_OBSERVATION,
            AuthorityClass.OBJECTIVE_CANON,
            AuthorityClass.HISTORICAL_RECORD,
        }))
    if authority is AuthorityClass.NPC_BELIEF:
        return AuthorityClass.NPC_BELIEF in actual
    if authority is AuthorityClass.FACTION_DOCTRINE:
        return AuthorityClass.FACTION_DOCTRINE in actual
    if authority is AuthorityClass.RUMOR:
        return AuthorityClass.RUMOR in actual
    if authority is AuthorityClass.DISPUTED_CLAIM:
        return bool(actual.intersection({AuthorityClass.DISPUTED_CLAIM, AuthorityClass.RUMOR}))
    if authority is AuthorityClass.SECRET_CANON:
        return AuthorityClass.SECRET_CANON in actual
    return True


def _semantic_support(claim: ClaimAssertion, supporting: Sequence[EvidenceRecord]) -> bool:
    claim_tokens = _tokens(claim.text)
    if not claim_tokens:
        return False
    evidence_tokens = _tokens(" ".join(record.content for record in supporting))
    if not evidence_tokens:
        return False
    overlap = len(claim_tokens.intersection(evidence_tokens))
    return overlap >= min(2, len(claim_tokens)) and overlap / len(claim_tokens) >= 0.2


def validate_claims(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
    blocks: Sequence[NarrativeBlock],
) -> tuple[tuple[ValidationIssue, ...], Mapping[str, Any]]:
    beats = {beat.beat_id: beat for beat in plan.beats}
    evidence_by_id = {record.evidence_id: record for record in evidence}
    ledger = claim_ledger(request)
    issues: list[ValidationIssue] = []
    claim_count = 0
    explicit_count = 0

    for block in blocks:
        beat = beats.get(block.beat_id)
        if beat is None:
            continue
        expected_scope = str(beat.metadata.get("evidence_scope") or "player")
        claims_by_id = {claim.claim_id: claim for claim in block.claims}
        missing = set(beat.required_claim_refs).difference(claims_by_id)
        if missing:
            issues.append(ValidationIssue(
                "missing_semantic_claim",
                f"Required semantic claims are missing: {sorted(missing)}",
                block.block_id,
            ))
        claim_source = str(block.metadata.get("claim_source") or "")
        for claim in block.claims:
            claim_count += 1
            explicit = claim_source in {"provider", "provider_repaired"} or claim.metadata.get(
                "claim_source"
            ) in {"provider", "provider_repaired"}
            explicit_count += int(explicit)
            unknown = set(claim.evidence_refs).difference(evidence_by_id)
            if unknown:
                issues.append(ValidationIssue(
                    "claim_unknown_evidence",
                    f"Claim references unknown evidence: {sorted(unknown)}",
                    block.block_id,
                ))
                continue
            if not set(claim.evidence_refs).issubset(block.evidence_refs):
                issues.append(ValidationIssue(
                    "claim_unplanned_evidence",
                    "Claim uses evidence outside the approved block grant.",
                    block.block_id,
                ))
            if claim.scope != expected_scope:
                issues.append(ValidationIssue(
                    "claim_scope_mismatch",
                    f"Claim scope {claim.scope!r} does not match beat scope {expected_scope!r}.",
                    block.block_id,
                ))
            supporting = [evidence_by_id[ref] for ref in claim.evidence_refs if ref in evidence_by_id]
            if not _authority_supported(claim.authority, supporting):
                issues.append(ValidationIssue(
                    "claim_authority_unsupported",
                    f"Claim authority {claim.authority.value} is unsupported by its evidence.",
                    block.block_id,
                ))
            expected = ledger.get(claim.claim_id)
            if expected:
                for field in ("subject_id", "predicate", "value"):
                    if field in expected and getattr(claim, field) != expected[field]:
                        issues.append(ValidationIssue(
                            "authoritative_claim_mismatch",
                            f"Claim {claim.claim_id} changed authoritative {field}.",
                            block.block_id,
                        ))
            elif claim.claim_id.startswith(_STATE_PREFIXES):
                issues.append(ValidationIssue(
                    "missing_authoritative_claim",
                    f"State claim {claim.claim_id} has no authoritative ledger entry.",
                    block.block_id,
                ))
            if explicit and not _semantic_support(claim, supporting):
                issues.append(ValidationIssue(
                    "unsupported_claim_text",
                    "Claim text is not semantically supported by its cited evidence.",
                    block.block_id,
                ))

    return tuple(issues), {
        "claim_count": claim_count,
        "explicit_claim_count": explicit_count,
        "semantic_grounding_passed": not issues,
    }
