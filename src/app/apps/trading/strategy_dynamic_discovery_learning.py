from __future__ import annotations

"""Post-close learning and qualification for dynamic discovery."""

from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import median
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .strategy_dynamic_discovery import (
    AttributionEvent,
    AttributionStage,
    DynamicCandidate,
    ShadowQualificationEvidence,
    TrendDurabilityOutcome,
    evaluate_shadow_qualification,
)


class DiscoveryDailyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    generated_at: datetime
    discovered_count: int
    active_count: int
    tier_a_count: int
    tier_b_count: int
    trigger_counts: dict[str, int]
    experiment_arm_counts: dict[str, int]
    attribution_counts: dict[str, int]
    strategy_signal_counts: dict[str, int]
    median_discovery_time: str | None = None
    durability_labeled_count: int = 0
    median_mfe_pct: float | None = None
    median_mae_pct: float | None = None
    plus_2r_before_minus_1r_rate: float | None = None
    top_candidates: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()


def build_daily_discovery_report(
    *,
    session_date: date,
    generated_at: datetime,
    candidates: Sequence[DynamicCandidate],
    attribution: Sequence[AttributionEvent] = (),
    outcomes: Sequence[TrendDurabilityOutcome] = (),
) -> DiscoveryDailyReport:
    trigger_counts: Counter[str] = Counter()
    arm_counts: Counter[str] = Counter()
    for candidate in candidates:
        trigger_counts.update(item.value for item in candidate.trigger_types)
        arm_counts.update(item.value for item in candidate.experiment_arms)
    attribution_counts = Counter(row.stage.value for row in attribution if row.passed)
    strategy_signals: Counter[str] = Counter(
        row.sub_strategy or "unassigned"
        for row in attribution
        if row.passed and row.stage in {AttributionStage.SIGNALLED, AttributionStage.TRADED}
    )
    discovery_times = sorted(row.discovered_at for row in candidates)
    mfe_values = [float(row.mfe_pct) for row in outcomes if row.mfe_pct is not None]
    mae_values = [float(row.mae_pct) for row in outcomes if row.mae_pct is not None]
    two_r = [row.plus_2r_before_minus_1r for row in outcomes if row.plus_2r_before_minus_1r is not None]
    ordered = sorted(candidates, key=lambda row: (-row.common_priority, row.discovered_at, row.instrument_id))
    top = tuple(
        {
            "instrument_id": row.instrument_id,
            "discovered_at": row.discovered_at.isoformat(),
            "lifecycle": row.lifecycle.value,
            "tier": row.tier.value,
            "attention_score": row.attention_score,
            "catalyst_score": row.catalyst_score,
            "common_priority": row.common_priority,
            "strategy_ranks": row.strategy_ranks,
            "trigger_types": [item.value for item in row.trigger_types],
        }
        for row in ordered[:15]
    )
    return DiscoveryDailyReport(
        session_date=session_date,
        generated_at=generated_at,
        discovered_count=len(candidates),
        active_count=sum(row.lifecycle.value == "active" for row in candidates),
        tier_a_count=sum(row.tier.value == "A" for row in candidates),
        tier_b_count=sum(row.tier.value == "B" for row in candidates),
        trigger_counts=dict(sorted(trigger_counts.items())),
        experiment_arm_counts=dict(sorted(arm_counts.items())),
        attribution_counts=dict(sorted(attribution_counts.items())),
        strategy_signal_counts=dict(sorted(strategy_signals.items())),
        median_discovery_time=(discovery_times[len(discovery_times) // 2].isoformat() if discovery_times else None),
        durability_labeled_count=len(outcomes),
        median_mfe_pct=median(mfe_values) if mfe_values else None,
        median_mae_pct=median(mae_values) if mae_values else None,
        plus_2r_before_minus_1r_rate=(sum(value is True for value in two_r) / len(two_r) if two_r else None),
        top_candidates=top,
        notes=(
            "Dynamic discovery is research-only and does not change AUTO PAPER authority.",
            "Frozen benchmark membership remains immutable for controlled comparisons.",
        ),
    )


def qualification_from_daily_reports(
    reports: Sequence[DiscoveryDailyReport],
    *,
    labeled_opportunities: int,
    discovery_recall: float,
    discovery_precision: float,
    median_discovery_latency_minutes: float | None,
    execution_adjusted_expectancy_r: float | None,
    max_drawdown_r: float | None,
    data_reliability_fraction: float,
    causality_violations: int = 0,
) -> ShadowQualificationEvidence:
    return evaluate_shadow_qualification(
        {
            "independent_sessions": len({row.session_date for row in reports}),
            "labeled_opportunities": labeled_opportunities,
            "discovery_recall": discovery_recall,
            "discovery_precision": discovery_precision,
            "median_discovery_latency_minutes": median_discovery_latency_minutes,
            "execution_adjusted_expectancy_r": execution_adjusted_expectancy_r,
            "max_drawdown_r": max_drawdown_r,
            "data_reliability_fraction": data_reliability_fraction,
            "causality_violations": causality_violations,
        }
    )


__all__ = [
    "DiscoveryDailyReport",
    "build_daily_discovery_report",
    "qualification_from_daily_reports",
]
