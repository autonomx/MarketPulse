from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .contracts import ResearchValidationReport, StrategyResearchFeatures


class ResearchPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    authoritative: bool
    reason_code: str
    score_adjustment: int = 0
    policy_version: str


def _feature_positive(feature: str, value: object) -> bool:
    """Return whether an observed feature value is favorable to a long setup."""

    if feature in {"immediate_supply_risk", "unresolved_supply"}:
        return value is False
    if feature in {
        "primary_catalyst_confirmed",
        "catalyst_same_day",
        "source_authority_sufficient",
    }:
        return value is True
    # Unknown promoted features fail conservative scoring rather than receiving a
    # favorable score by accident. HTR-15 only promotes explicitly reviewed
    # validation features, but this keeps future schema additions fail-safe.
    return False


def evaluate_research_policy(
    *,
    strategy_version: str,
    features: StrategyResearchFeatures | None,
    validation: ResearchValidationReport | None,
    policy_version: str = "trading-research-1",
) -> ResearchPolicyDecision:
    """Evaluate the versioned deterministic HTR policy.

    1.0/1.1 remain research-non-authoritative. 1.2 requires a separately
    reviewed validation artifact. Recommendation semantics are intentionally
    distinct:

    * observe_only: no decision effect;
    * score_only: favorable/unfavorable evidence contributes +1/-1 quality;
    * soft_gate: unfavorable or missing evidence contributes -2 quality while a
      favorable observation adds no bonus;
    * hard_gate: the required favorable state must be present or authorization
      fails immediately.

    The quality threshold itself is applied by ``apply_research_policy_to_quality``
    so AUTO PAPER and backtests consume the exact same rule.
    """

    if strategy_version in {"1.0.0", "1.1.0"}:
        return ResearchPolicyDecision(
            allowed=True,
            authoritative=False,
            reason_code="LEGACY_RESEARCH_NON_AUTHORITATIVE",
            policy_version="legacy-1",
        )
    if strategy_version != "1.2.0":
        return ResearchPolicyDecision(
            allowed=False,
            authoritative=True,
            reason_code="UNSUPPORTED_RESEARCH_STRATEGY_VERSION",
            policy_version=policy_version,
        )
    if validation is None or validation.policy_version != policy_version or not validation.promotion_allowed:
        return ResearchPolicyDecision(
            allowed=False,
            authoritative=True,
            reason_code="RESEARCH_POLICY_NOT_VALIDATED",
            policy_version=policy_version,
        )
    if features is None:
        return ResearchPolicyDecision(
            allowed=False,
            authoritative=True,
            reason_code="RESEARCH_FEATURES_UNAVAILABLE",
            policy_version=policy_version,
        )

    score_adjustment = 0
    for result in validation.feature_results:
        recommendation = result.recommendation
        if recommendation == "observe_only":
            continue

        value = getattr(features, result.feature, None)
        positive = _feature_positive(result.feature, value)

        if recommendation == "hard_gate":
            # Hard gates fail closed. A missing/None value is not equivalent to a
            # clean negative risk flag or a confirmed positive catalyst flag.
            if not positive:
                return ResearchPolicyDecision(
                    allowed=False,
                    authoritative=True,
                    reason_code=f"RESEARCH_HARD_GATE_{result.feature.upper()}",
                    policy_version=policy_version,
                )
            continue

        if recommendation == "score_only":
            score_adjustment += 1 if positive else -1
            continue

        if recommendation == "soft_gate":
            # A soft gate is deliberately asymmetric: clean evidence does not
            # inflate setup quality, while failed/missing evidence penalizes a
            # marginal setup strongly enough to matter at the quality boundary.
            if not positive:
                score_adjustment -= 2

    return ResearchPolicyDecision(
        allowed=True,
        authoritative=True,
        reason_code="RESEARCH_POLICY_PASS",
        score_adjustment=score_adjustment,
        policy_version=policy_version,
    )
