from __future__ import annotations

"""Deterministic, research-only intraday learning features.

The learning snapshot answers a different question from execution eligibility:
"What is the tape doing now, and how should the morning prior be updated?"

It never authorizes an order. The existing versioned strategy evaluator, server
risk gates, and execution-eligibility contract remain the only AUTO PAPER path.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .gapper_dataset import GapperCandidate
from .models import MarketBar
from .strategies.gap_pullback import _regular_bars, session_vwap
from .strategies.models import GapPullbackResult


IntradayPattern = Literal[
    "unresolved",
    "trend_continuation",
    "gap_hold",
    "opening_fade_recovery",
    "failed_selloff_watch",
    "squeeze_momentum",
    "distribution_fade",
    "high_variance",
]


class IntradayLearningSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalyst_quality_score: int = Field(ge=0, le=10)
    supply_risk_score: int = Field(ge=0, le=10)
    float_structure_risk_score: int = Field(ge=0, le=10)
    extension_risk_score: int = Field(ge=0, le=10)
    squeeze_probability_score: int = Field(ge=0, le=10)
    failed_selloff_probability_score: int = Field(ge=0, le=10)
    trend_continuation_score: int = Field(ge=0, le=10)
    gap_retention_score: int = Field(ge=0, le=10)
    execution_quality_score: int = Field(ge=0, le=10)
    opportunity_score: int = Field(ge=0, le=10)
    raw_movement_score: int = Field(ge=0, le=10)
    execution_adjusted_opportunity_score: int = Field(ge=0, le=10)
    pattern: IntradayPattern
    current_price: Decimal
    session_open: Decimal
    session_high: Decimal
    session_low: Decimal
    session_vwap: Decimal | None
    close_location: Decimal | None
    gap_retention_ratio: Decimal | None
    turnover_to_float: Decimal | None
    current_vs_premarket_pct: Decimal
    session_return_pct: Decimal
    deterministic_state: str
    deterministic_reason_code: str
    execution_authority: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def backfill_split_opportunity_scores(cls, value):
        """Keep pre-split snapshots/events readable with the old ranking semantics.

        Before the raw/execution-adjusted split, `opportunity_score` was the
        primary rank followed by execution quality. Mapping both new scores to
        the old opportunity value preserves that ordering for historical
        evidence and test fixtures instead of silently assigning zero.
        """
        if not isinstance(value, dict):
            return value
        if "opportunity_score" not in value:
            return value
        updated = dict(value)
        updated.setdefault("raw_movement_score", updated["opportunity_score"])
        updated.setdefault(
            "execution_adjusted_opportunity_score",
            updated["opportunity_score"],
        )
        return updated


def _clamp(value: int) -> int:
    return max(0, min(10, int(value)))


def _extension_risk(gap_pct: Decimal) -> int:
    if gap_pct < Decimal("40"):
        return 2
    if gap_pct < Decimal("60"):
        return 4
    if gap_pct < Decimal("100"):
        return 6
    if gap_pct < Decimal("200"):
        return 8
    return 10


def _float_risk(float_shares: Decimal | None) -> int:
    if float_shares is None:
        return 5
    if float_shares < Decimal("1000000"):
        return 10
    if float_shares < Decimal("2000000"):
        return 9
    if float_shares < Decimal("5000000"):
        return 7
    if float_shares <= Decimal("30000000"):
        return 5
    if float_shares <= Decimal("100000000"):
        return 3
    return 1


def _catalyst_quality(candidate: GapperCandidate) -> int:
    count = len(candidate.catalyst_evidence_ids)
    if count <= 0:
        return 2
    return _clamp(6 + min(4, count))


def _supply_risk(candidate: GapperCandidate) -> int:
    if candidate.dilution_flags:
        return _clamp(6 + min(4, len(candidate.dilution_flags)))
    # Absence of a typed flag is not proof that supply is clean.
    return 3 if candidate.catalyst_evidence_ids else 5


def _close_location(current: Decimal, low: Decimal, high: Decimal) -> Decimal | None:
    width = high - low
    if width <= 0:
        return None
    return (current - low) / width


def _gap_retention(candidate: GapperCandidate, current: Decimal) -> tuple[Decimal | None, int]:
    current_gap = (current / candidate.previous_close - Decimal("1")) * Decimal("100")
    if candidate.gap_pct <= 0:
        return None, 0
    ratio = current_gap / candidate.gap_pct
    if ratio >= Decimal("1.10"):
        score = 10
    elif ratio >= Decimal("0.90"):
        score = 9
    elif ratio >= Decimal("0.75"):
        score = 8
    elif ratio >= Decimal("0.60"):
        score = 6
    elif ratio >= Decimal("0.40"):
        score = 4
    elif ratio > 0:
        score = 2
    else:
        score = 0
    return ratio, score


def _recent_direction(bars: list[MarketBar]) -> tuple[int, int]:
    recent = bars[-6:]
    if len(recent) < 2:
        return 0, 0
    up_closes = sum(1 for left, right in zip(recent, recent[1:]) if right.close > left.close)
    rising_lows = sum(1 for left, right in zip(recent, recent[1:]) if right.low > left.low)
    return up_closes, rising_lows


def build_intraday_learning_snapshot(
    candidate: GapperCandidate,
    result: GapPullbackResult,
    bars: list[MarketBar] | tuple[MarketBar, ...],
) -> IntradayLearningSnapshot:
    regular = _regular_bars(list(bars))
    if not regular:
        raise ValueError("intraday learning requires at least one finalized regular-session bar")

    current = regular[-1].close
    session_open = regular[0].open
    session_high = max(bar.high for bar in regular)
    session_low = min(bar.low for bar in regular)
    cumulative_volume = sum((bar.volume for bar in regular), Decimal("0"))
    vwap = session_vwap(regular)
    location = _close_location(current, session_low, session_high)
    retention_ratio, retention_score = _gap_retention(candidate, current)
    turnover = (
        cumulative_volume / candidate.float_shares
        if candidate.float_shares is not None and candidate.float_shares > 0
        else None
    )
    current_vs_premarket = (current / candidate.premarket_price - Decimal("1")) * Decimal("100")
    session_return = (current / session_open - Decimal("1")) * Decimal("100")
    up_closes, rising_lows = _recent_direction(regular)

    catalyst = _catalyst_quality(candidate)
    supply = _supply_risk(candidate)
    float_risk = _float_risk(candidate.float_shares)
    extension = _extension_risk(candidate.gap_pct)

    squeeze = 0
    if turnover is not None:
        if turnover >= Decimal("5"):
            squeeze += 4
        elif turnover >= Decimal("2"):
            squeeze += 3
        elif turnover >= Decimal("1"):
            squeeze += 2
        elif turnover >= Decimal("0.5"):
            squeeze += 1
    elif cumulative_volume >= max(candidate.premarket_volume, Decimal("1")) * Decimal("0.5"):
        squeeze += 1
    if session_return >= Decimal("25"):
        squeeze += 3
    elif session_return >= Decimal("10"):
        squeeze += 2
    elif session_return >= Decimal("3"):
        squeeze += 1
    if vwap is not None and current >= vwap:
        squeeze += 2
    if location is not None and location >= Decimal("0.75"):
        squeeze += 1
    squeeze = _clamp(squeeze)

    failed_selloff = 0
    pullback_from_high = (session_high - session_low) / session_high * Decimal("100") if session_high > 0 else Decimal("0")
    recovery_from_low = (current / session_low - Decimal("1")) * Decimal("100") if session_low > 0 else Decimal("0")
    if pullback_from_high >= Decimal("5"):
        failed_selloff += 2
    if recovery_from_low >= Decimal("5"):
        failed_selloff += 2
    if vwap is not None and current >= vwap:
        failed_selloff += 2
    if rising_lows >= 3:
        failed_selloff += 2
    elif rising_lows >= 2:
        failed_selloff += 1
    if "higher_low_confirmed" in result.transitions:
        failed_selloff += 2
    elif "bounce_high_confirmed" in result.transitions:
        failed_selloff += 1
    failed_selloff = _clamp(failed_selloff)

    trend = 0
    if current >= candidate.premarket_price:
        trend += 2
    if vwap is not None and current >= vwap:
        trend += 2
    if location is not None and location >= Decimal("0.75"):
        trend += 2
    elif location is not None and location >= Decimal("0.55"):
        trend += 1
    if up_closes >= 4:
        trend += 2
    elif up_closes >= 3:
        trend += 1
    if retention_score >= 8:
        trend += 2
    elif retention_score >= 6:
        trend += 1
    trend = _clamp(trend)

    spread = candidate.spread_bps
    if spread is None:
        execution_quality = 3
    elif spread <= Decimal("25"):
        execution_quality = 10
    elif spread <= Decimal("50"):
        execution_quality = 9
    elif spread <= Decimal("100"):
        execution_quality = 7
    elif spread <= Decimal("150"):
        execution_quality = 5
    elif spread <= Decimal("300"):
        execution_quality = 2
    else:
        execution_quality = 0

    opportunity = max(squeeze, failed_selloff, trend, retention_score)
    raw_movement = opportunity
    # Keep "how much can this move?" separate from "how attractive is this
    # to execute?". A high-variance microcap may score very highly on raw
    # movement while remaining a poor execution environment. This score is
    # research-only and never authorizes an order.
    execution_adjusted = _clamp(
        round(
            (raw_movement * 0.65)
            + (execution_quality * 0.35)
            - (float_risk * 0.15)
            - (supply * 0.10)
            - (extension * 0.10)
        )
    )

    if location is not None and location <= Decimal("0.25") and retention_score <= 3:
        pattern: IntradayPattern = "distribution_fade"
    elif squeeze >= 8 and float_risk >= 7:
        pattern = "squeeze_momentum"
    elif failed_selloff >= 8:
        pattern = "failed_selloff_watch"
    elif pullback_from_high >= Decimal("8") and recovery_from_low >= Decimal("8") and failed_selloff >= 6:
        pattern = "opening_fade_recovery"
    elif trend >= 8:
        pattern = "trend_continuation"
    elif retention_score >= 8:
        pattern = "gap_hold"
    elif float_risk >= 8 and (session_high - session_low) / max(session_open, Decimal("0.01")) >= Decimal("0.20"):
        pattern = "high_variance"
    else:
        pattern = "unresolved"

    return IntradayLearningSnapshot(
        catalyst_quality_score=catalyst,
        supply_risk_score=supply,
        float_structure_risk_score=float_risk,
        extension_risk_score=extension,
        squeeze_probability_score=squeeze,
        failed_selloff_probability_score=failed_selloff,
        trend_continuation_score=trend,
        gap_retention_score=retention_score,
        execution_quality_score=execution_quality,
        opportunity_score=opportunity,
        raw_movement_score=raw_movement,
        execution_adjusted_opportunity_score=execution_adjusted,
        pattern=pattern,
        current_price=current,
        session_open=session_open,
        session_high=session_high,
        session_low=session_low,
        session_vwap=vwap,
        close_location=location,
        gap_retention_ratio=retention_ratio,
        turnover_to_float=turnover,
        current_vs_premarket_pct=current_vs_premarket,
        session_return_pct=session_return,
        deterministic_state=result.state,
        deterministic_reason_code=result.reason_code,
        execution_authority=False,
    )


__all__ = ["IntradayLearningSnapshot", "build_intraday_learning_snapshot"]
