"""Fail-closed validation, bounded repair, and deterministic fallback."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Sequence

from .authority import BeatKind
from .claims import infer_claims, validate_claims
from .contracts import (
    EvidenceRecord,
    NarrativeBlock,
    TurnPresentationRequest,
    ValidationIssue,
    ValidationReport,
)
from .planner import NarrativePlan
from .writer import DeterministicNarrativeWriter, NarrativeWriter, WriterResult

logger = logging.getLogger(__name__)
# When a turn requires model-written prose and the model fails at runtime (an error,
# or prose that fails validation after repair), the turn publishes validated
# deterministic prose marked with this flag instead of failing after its state
# is committed. A writer that is not a live provider at all is still refused.
PROVIDER_REQUIRED_FALLBACK = "provider_required_fallback"

_TRUNCATION_MARKERS = ("...[truncated]", "[truncated]", "<truncated>")
_LABEL_PREFIX = re.compile(r"^(?:narrator|action|npc|result|reward)\s*:\s*", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_REPAIRABLE_PROVIDER_CLAIM_ISSUES = {
    "claim_unplanned_evidence",
    "claim_scope_mismatch",
    "claim_authority_unsupported",
    "unsupported_claim_text",
}


def _scripts(text: str) -> set[str]:
    found: set[str] = set()
    for char in text:
        code = ord(char)
        if "A" <= char <= "Z" or "a" <= char <= "z":
            found.add("latin")
        elif 0x0400 <= code <= 0x04FF:
            found.add("cyrillic")
        elif 0x0600 <= code <= 0x06FF:
            found.add("arabic")
        elif 0xAC00 <= code <= 0xD7AF:
            found.add("hangul")
        elif 0x4E00 <= code <= 0x9FFF:
            found.add("han")
    return found


def _allowed_claim_refs(request: TurnPresentationRequest) -> set[str]:
    raw = request.authoritative_outcome.get("allowed_claim_refs") or request.metadata.get("allowed_claim_refs") or ()
    return {str(value) for value in raw if str(value).strip()}


def _word_count(blocks: Sequence[NarrativeBlock]) -> int:
    return sum(len(block.text.split()) for block in blocks)


class NarrativeValidator:
    """Validate structure, evidence, knowledge, claims, agency, and output quality."""

    def validate(
        self,
        request: TurnPresentationRequest,
        plan: NarrativePlan,
        evidence: Sequence[EvidenceRecord],
        blocks: Sequence[NarrativeBlock],
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        expected = {beat.beat_id: beat for beat in plan.beats}
        evidence_ids = {record.evidence_id for record in evidence}
        allowed_claims = _allowed_claim_refs(request)
        valid_speakers = set(request.actor_ids)
        if request.target_actor_id:
            valid_speakers.add(request.target_actor_id)
        seen_beats: set[str] = set()
        seen_text: set[str] = set()

        for block in blocks:
            beat = expected.get(block.beat_id)
            if beat is None:
                issues.append(ValidationIssue("unplanned_block", "Block does not map to an approved beat.", block.block_id))
                continue
            if block.beat_id in seen_beats:
                issues.append(ValidationIssue("duplicate_beat", "A planned beat was written more than once.", block.block_id))
            seen_beats.add(block.beat_id)
            if block.sequence != beat.sequence or block.kind is not beat.kind or block.purpose is not beat.purpose:
                issues.append(ValidationIssue("beat_contract_changed", "Block changed the approved beat contract.", block.block_id))
            if block.speaker_id != beat.speaker_id:
                issues.append(ValidationIssue("speaker_changed", "Block changed the approved speaker.", block.block_id))
            if block.kind is BeatKind.DIALOGUE and (not block.speaker_id or block.speaker_id not in valid_speakers):
                issues.append(ValidationIssue("invalid_speaker", "Dialogue speaker is not present or permitted.", block.block_id))
            unknown_evidence = set(block.evidence_refs).difference(evidence_ids)
            if unknown_evidence:
                issues.append(ValidationIssue("unknown_evidence", f"Unknown evidence references: {sorted(unknown_evidence)}", block.block_id))
            if not set(block.evidence_refs).issubset(set(beat.evidence_refs)):
                issues.append(ValidationIssue("unplanned_evidence", "Block references evidence not approved for its beat.", block.block_id))
            unknown_claims = set(block.claim_refs).difference(allowed_claims)
            if unknown_claims:
                issues.append(ValidationIssue("unapproved_claim", f"Unapproved claim references: {sorted(unknown_claims)}", block.block_id))
            if not set(beat.required_claim_refs).issubset(set(block.claim_refs)):
                issues.append(ValidationIssue("missing_claim_ref", "Block omitted a required authoritative claim reference.", block.block_id))
            text = block.text.strip()
            if not text:
                issues.append(ValidationIssue("empty_block", "Block text is empty.", block.block_id))
                continue
            normalized = " ".join(text.casefold().split())
            if normalized in seen_text:
                issues.append(ValidationIssue("duplicate_text", "Visible text repeats another block.", block.block_id))
            seen_text.add(normalized)
            if _LABEL_PREFIX.match(text):
                issues.append(ValidationIssue("legacy_label_prefix", "Visible prose contains a legacy presentation label.", block.block_id))
            if "�" in text or any(marker in text.casefold() for marker in _TRUNCATION_MARKERS):
                issues.append(ValidationIssue("malformed_or_truncated", "Output contains malformed or truncated text.", block.block_id))
            scripts = _scripts(text)
            if "latin" in scripts and len(scripts) > 1 and not block.metadata.get("allow_multiscript"):
                issues.append(ValidationIssue("mixed_script_corruption", f"Unexpected mixed scripts: {sorted(scripts)}", block.block_id))
            if block.kind is BeatKind.DIALOGUE and text.count('"') % 2 == 1:
                issues.append(ValidationIssue("unbalanced_quote", "Dialogue contains an unbalanced quote.", block.block_id))

        claim_issues, claim_metadata = validate_claims(
            request,
            plan,
            evidence,
            blocks,
        )
        issues.extend(claim_issues)
        required_beats = {beat.beat_id for beat in plan.beats if beat.required}
        missing = required_beats.difference(seen_beats)
        if missing:
            issues.append(ValidationIssue("missing_required_beats", f"Missing required beats: {sorted(missing)}"))
        sequences = [block.sequence for block in blocks]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            issues.append(ValidationIssue("invalid_sequence", "Blocks are not in a unique ascending sequence."))
        if _word_count(blocks) > plan.word_budget[1]:
            issues.append(ValidationIssue("word_budget_exceeded", "Response exceeds the configured maximum word budget."))
        return ValidationReport(
            passed=not issues,
            issues=tuple(issues),
            metadata={
                **dict(claim_metadata),
                "grounding_passed": not issues,
            },
        )


class NarrativeRepairer:
    """Apply only mechanically safe repairs that do not invent new meaning."""

    def repair(self, plan: NarrativePlan, blocks: Sequence[NarrativeBlock]) -> tuple[tuple[NarrativeBlock, ...], tuple[str, ...]]:
        expected = {beat.beat_id: beat for beat in plan.beats}
        repaired: list[NarrativeBlock] = []
        history: list[str] = []
        seen_text: set[str] = set()
        for block in sorted(blocks, key=lambda item: (item.sequence, item.block_id)):
            beat = expected.get(block.beat_id)
            if beat is None:
                history.append(f"removed_unplanned:{block.block_id}")
                continue
            text = _WHITESPACE.sub(" ", _LABEL_PREFIX.sub("", block.text)).strip()
            normalized = " ".join(text.casefold().split())
            if normalized in seen_text:
                purpose = beat.purpose.value.replace("_", " ").capitalize()
                text = f"{purpose}: {text}"
                normalized = " ".join(text.casefold().split())
                history.append(f"contextualized_duplicate:{block.block_id}")
            seen_text.add(normalized)
            if text != block.text and not any(item.endswith(block.block_id) for item in history):
                history.append(f"normalized_text:{block.block_id}")
            repaired.append(
                replace(
                    block,
                    sequence=beat.sequence,
                    kind=beat.kind,
                    purpose=beat.purpose,
                    speaker_id=beat.speaker_id,
                    evidence_refs=beat.evidence_refs,
                    claim_refs=beat.required_claim_refs,
                    text=text,
                )
            )
        return tuple(sorted(repaired, key=lambda block: block.sequence)), tuple(history)


@dataclass(frozen=True)
class ValidatedWriterResult:
    writer_result: WriterResult
    validation: ValidationReport
    fallback_used: bool


class NarrativeProviderRequiredError(RuntimeError):
    """Raised when live dialogue prose cannot be produced by the provider."""


def _fallback_diagnostics(
    result: ValidatedWriterResult,
    *,
    reason: str,
    issues: Sequence[ValidationIssue] = (),
    provider_required: bool = False,
) -> ValidatedWriterResult:
    metadata = dict(result.writer_result.raw_metadata or {})
    metadata["provider_fallback_reason"] = reason
    if provider_required:
        metadata[PROVIDER_REQUIRED_FALLBACK] = True
    if issues:
        metadata["provider_validation_issues"] = sorted(
            {str(issue.code) for issue in issues if str(issue.code).strip()}
        )
    return replace(
        result,
        writer_result=replace(result.writer_result, raw_metadata=metadata),
    )


def _repair_provider_claims(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
    blocks: Sequence[NarrativeBlock],
    report: ValidationReport,
    validator: NarrativeValidator,
) -> tuple[tuple[NarrativeBlock, ...], ValidationReport] | None:
    issue_codes = {issue.code for issue in report.issues}
    if not issue_codes or not issue_codes.issubset(_REPAIRABLE_PROVIDER_CLAIM_ISSUES):
        return None
    stripped = tuple(
        replace(
            block,
            claims=(),
            metadata={
                **dict(block.metadata),
                "provider_claim_repair": True,
            },
        )
        for block in blocks
    )
    repaired = infer_claims(request, plan, evidence, stripped)
    repaired_report = validator.validate(request, plan, evidence, repaired)
    return repaired, repaired_report


def _prepare(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
    result: WriterResult,
) -> WriterResult:
    return replace(
        result,
        blocks=infer_claims(request, plan, evidence, result.blocks),
    )


def _validated_fallback(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
    fallback_writer: NarrativeWriter,
    validator: NarrativeValidator,
    repairer: NarrativeRepairer,
) -> ValidatedWriterResult:
    fallback = _prepare(
        request,
        plan,
        evidence,
        fallback_writer.write(request, plan, evidence),
    )
    report = validator.validate(request, plan, evidence, fallback.blocks)
    if report.passed:
        return ValidatedWriterResult(fallback, report, True)
    repaired, history = repairer.repair(plan, fallback.blocks)
    repaired = infer_claims(request, plan, evidence, repaired)
    repaired_report = validator.validate(request, plan, evidence, repaired)
    return ValidatedWriterResult(
        replace(fallback, blocks=repaired),
        replace(repaired_report, repair_history=history),
        True,
    )


_REVISION_GUIDANCE = {
    "unsupported_claim_text": (
        "A claim's text is not supported by its cited evidence. Each claim must restate what its "
        "approved_evidence says, close to its wording; mood and gesture may color the prose but are "
        "not claims, and no new facts may appear."
    ),
    "claim_scope_mismatch": "Each claim's scope must equal its beat's evidence_scope.",
    "missing_semantic_claim": "Include a claim for every required_claim_id in the beat's claim_contract.",
    "missing_claim_ref": "Include a claim for every required_claim_id in the beat's claim_contract.",
    "claim_unknown_evidence": "Cite only evidence IDs from that beat's approved_evidence.",
    "claim_unplanned_evidence": "Cite only evidence IDs from that beat's approved_evidence.",
    "unknown_evidence": "Cite only evidence IDs from that beat's approved_evidence.",
    "unplanned_evidence": "Cite only evidence IDs from that beat's approved_evidence.",
    "claim_authority_unsupported": "Give each claim the authority its evidence carries.",
    "duplicate_text": "Do not repeat a sentence in more than one block.",
}


def revision_feedback(report: ValidationReport) -> dict[str, Any]:
    """What the writer is told after its prose failed validation (one revision per turn)."""
    issues = [
        {"code": issue.code, "problem": issue.message, "block_id": issue.block_id}
        for issue in report.issues
    ]
    guidance = list(dict.fromkeys(_REVISION_GUIDANCE.get(issue.code, issue.message) for issue in report.issues))
    return {"reason": "validation_failed", "failed_checks": issues, "instructions": guidance}


def _validate_and_repair(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
    result: WriterResult,
    validator: NarrativeValidator,
    repairer: NarrativeRepairer,
) -> tuple[ValidatedWriterResult | None, ValidationReport]:
    """The validated result after deterministic and claim repair, or the last failing report."""
    report = validator.validate(request, plan, evidence, result.blocks)
    if report.passed:
        return ValidatedWriterResult(result, report, False), report
    repaired, history = repairer.repair(plan, result.blocks)
    repaired = infer_claims(request, plan, evidence, repaired)
    repaired_report = validator.validate(request, plan, evidence, repaired)
    if repaired_report.passed:
        return ValidatedWriterResult(
            replace(result, blocks=repaired),
            replace(repaired_report, repair_history=history),
            False,
        ), repaired_report
    claim_repair = _repair_provider_claims(request, plan, evidence, repaired, repaired_report, validator)
    if claim_repair is not None:
        claim_repaired, claim_report = claim_repair
        if claim_report.passed:
            return ValidatedWriterResult(
                replace(result, blocks=claim_repaired),
                replace(claim_report, repair_history=(*history, "repaired_provider_claims")),
                False,
            ), claim_report
    return None, repaired_report


def _revise_with_feedback(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
    writer: NarrativeWriter,
    validator: NarrativeValidator,
    repairer: NarrativeRepairer,
    failed: ValidationReport,
) -> ValidatedWriterResult | None:
    """Ask the provider once more, telling it which checks failed; None if that fails too."""
    feedback = revision_feedback(failed)
    revised_request = replace(
        request,
        metadata={**dict(request.metadata), "narrative_revision_feedback": feedback},
    )
    try:
        revised = _prepare(revised_request, plan, evidence, writer.write(revised_request, plan, evidence))
    except Exception:
        logger.warning("rpg narrative revision call failed", exc_info=True)
        return None
    validated, still_failing = _validate_and_repair(request, plan, evidence, revised, validator, repairer)
    if validated is None:
        logger.info(
            "rpg narrative revision still failed validation (%s)",
            ",".join(sorted({issue.code for issue in still_failing.issues})),
        )
        return None
    metadata = dict(validated.writer_result.raw_metadata or {})
    metadata["provider_revision_feedback"] = sorted({str(issue["code"]) for issue in feedback["failed_checks"]})
    return replace(
        validated,
        writer_result=replace(validated.writer_result, raw_metadata=metadata),
        validation=replace(
            validated.validation,
            repair_history=("provider_revision_with_feedback", *validated.validation.repair_history),
        ),
    )


def write_validate_repair(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
    writer: NarrativeWriter,
    *,
    validator: NarrativeValidator | None = None,
    repairer: NarrativeRepairer | None = None,
    fallback_writer: NarrativeWriter | None = None,
    allow_deterministic_fallback: bool = True,
) -> ValidatedWriterResult:
    validator = validator or NarrativeValidator()
    repairer = repairer or NarrativeRepairer()
    fallback_writer = fallback_writer or DeterministicNarrativeWriter()
    try:
        result = _prepare(
            request,
            plan,
            evidence,
            writer.write(request, plan, evidence),
        )
    except Exception as exc:
        if not allow_deterministic_fallback:
            logger.warning("rpg narrative provider failed; publishing validated fallback prose", exc_info=True)
        fallback = _validated_fallback(
            request,
            plan,
            evidence,
            fallback_writer,
            validator,
            repairer,
        )
        return _fallback_diagnostics(
            fallback,
            reason=f"writer_exception:{type(exc).__name__}",
            provider_required=not allow_deterministic_fallback,
        )
    if not allow_deterministic_fallback and result.source != "structured_provider":
        raise NarrativeProviderRequiredError(
            "LLM-authored dialogue is required; deterministic prose is not publishable"
        )

    validated, failed = _validate_and_repair(request, plan, evidence, result, validator, repairer)
    if validated is not None:
        return validated
    if result.source == "structured_provider":
        revised = _revise_with_feedback(request, plan, evidence, writer, validator, repairer, failed)
        if revised is not None:
            return revised
    repaired_report = failed
    if not allow_deterministic_fallback:
        logger.warning(
            "rpg narrative prose failed validation after repair (%s); publishing validated fallback prose",
            ",".join(sorted({issue.code for issue in repaired_report.issues})),
        )
    fallback = _validated_fallback(
        request,
        plan,
        evidence,
        fallback_writer,
        validator,
        repairer,
    )
    return _fallback_diagnostics(
        fallback,
        reason="provider_validation_failed_after_repair",
        issues=repaired_report.issues,
        provider_required=not allow_deterministic_fallback,
    )
