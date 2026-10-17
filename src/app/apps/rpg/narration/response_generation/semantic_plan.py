from __future__ import annotations

from dataclasses import dataclass

from .claim_ledger import ClaimLedger
from .contracts import SectionType, SemanticResponsePlan
from typing import Any


_FACTUAL_TYPES = {
    SectionType.ACTION,
    SectionType.NPC_DIALOGUE,
    SectionType.RESULT,
    SectionType.STATE_CHANGE,
}


@dataclass(frozen=True)
class SemanticPlanValidation:
    ok: bool
    issues: tuple[str, ...]
    referenced_claims: tuple[str, ...]
    referenced_soft_truth: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": list(self.issues),
            "referenced_claims": list(self.referenced_claims),
            "referenced_soft_truth": list(self.referenced_soft_truth),
        }


def validate_semantic_plan(
    plan: SemanticResponsePlan,
    ledger: ClaimLedger,
    *,
    allowed_soft_truth_refs: tuple[str, ...] = (),
) -> SemanticPlanValidation:
    issues: list[str] = []
    claim_refs: list[str] = []
    soft_refs: list[str] = []
    allowed_soft = set(allowed_soft_truth_refs)
    prohibited = set(ledger.prohibited_claim_refs)

    for section in plan.sections:
        factual = section.section_type in _FACTUAL_TYPES or bool(section.metadata.get("factual"))
        if factual and not section.claim_refs and not section.soft_truth_refs:
            issues.append(f"missing_reference:{section.section_id}")
        for claim_ref in section.claim_refs:
            if claim_ref not in claim_refs:
                claim_refs.append(claim_ref)
            if claim_ref in prohibited:
                issues.append(f"prohibited_claim:{section.section_id}:{claim_ref}")
            elif not ledger.contains(claim_ref):
                issues.append(f"unknown_claim:{section.section_id}:{claim_ref}")
        for soft_ref in section.soft_truth_refs:
            if soft_ref not in soft_refs:
                soft_refs.append(soft_ref)
            if soft_ref not in allowed_soft:
                issues.append(f"unknown_soft_truth:{section.section_id}:{soft_ref}")
        if section.section_type is SectionType.NPC_DIALOGUE:
            if not section.speaker_id:
                issues.append(f"missing_speaker:{section.section_id}")
            known = set(ledger.speaker_knowledge_refs.get(section.speaker_id, ()))
            if known:
                for claim_ref in section.claim_refs:
                    if claim_ref not in known:
                        issues.append(
                            f"speaker_claim_out_of_scope:{section.section_id}:{claim_ref}"
                        )

    return SemanticPlanValidation(
        ok=not issues,
        issues=tuple(issues),
        referenced_claims=tuple(claim_refs),
        referenced_soft_truth=tuple(soft_refs),
    )
