from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal

from .contracts import ResearchValidationReport, ValidationFeatureResult, fingerprint

Recommendation = Literal["observe_only", "score_only", "soft_gate", "hard_gate"]
_STRENGTH: dict[str, int] = {
    "observe_only": 0,
    "score_only": 1,
    "soft_gate": 2,
    "hard_gate": 3,
}


def create_reviewed_validation_report(
    source: ResearchValidationReport,
    *,
    approved_recommendations: dict[str, Recommendation],
    review_note: str,
) -> ResearchValidationReport:
    """Create the only artifact capable of unlocking HTR-15 authority.

    A reviewer may preserve or *reduce* the validator's recommended authority for
    a feature, never strengthen it. Unlisted features are demoted to observe-only.
    The automatic HTR-14 report remains immutable and non-promoted.
    """
    note = " ".join(str(review_note or "").split()).strip()
    if len(note) < 10:
        raise ValueError("research_validation_review_note_too_short")
    if not approved_recommendations:
        raise ValueError("research_validation_review_requires_feature_selection")

    source_by_feature = {item.feature: item for item in source.feature_results}
    unknown = sorted(set(approved_recommendations) - set(source_by_feature))
    if unknown:
        raise ValueError("research_validation_unknown_features:" + ",".join(unknown))

    reviewed: list[ValidationFeatureResult] = []
    active = 0
    for item in source.feature_results:
        requested: Recommendation = approved_recommendations.get(item.feature, "observe_only")
        if _STRENGTH[requested] > _STRENGTH[item.recommendation]:
            raise ValueError(
                f"research_validation_cannot_strengthen:{item.feature}:"
                f"{item.recommendation}->{requested}"
            )
        active += int(requested != "observe_only")
        reviewed.append(item.model_copy(update={
            "recommendation": requested,
            "reason": (
                f"reviewed from {source.validation_id}; approved={requested}; "
                f"validator_recommended={item.recommendation}; {item.reason}"
            ),
        }))
    if active == 0:
        raise ValueError("research_validation_review_selected_no_active_features")

    generated = datetime.now(timezone.utc)
    payload = {
        "source_validation_id": source.validation_id,
        "policy_version": source.policy_version,
        "sample_size": source.sample_size,
        "exact_sample_size": source.exact_sample_size,
        "feature_results": [item.model_dump(mode="json") for item in reviewed],
        "review_note": note,
        "promotion_allowed": True,
    }
    fp = fingerprint(payload)
    return ResearchValidationReport(
        validation_id=f"rval-reviewed-{hashlib.sha256(fp.encode()).hexdigest()[:20]}",
        policy_version=source.policy_version,
        generated_at=generated,
        sample_size=source.sample_size,
        exact_sample_size=source.exact_sample_size,
        feature_results=tuple(reviewed),
        promotion_allowed=True,
        notes=(
            *source.notes,
            f"Explicit HTR-15 review of {source.validation_id}: {note}",
            "Review preserved/demoted validator recommendations only; no feature authority was strengthened.",
        ),
        immutable_fingerprint=fp,
    )


__all__ = ["Recommendation", "create_reviewed_validation_report"]
