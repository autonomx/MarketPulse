from __future__ import annotations

"""Causal entry-quality confluence for successor-strategy research.

This module scores information already known at a finalized structural signal.
It has no order, risk, or promotion authority and does not alter frozen V2.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .indicator_signals import IndicatorSnapshot


MINIMUM_GAP_PCT = Decimal("30")
MAXIMUM_BASE_RANGE_PCT = Decimal("8")
MINIMUM_BREAKOUT_VOLUME_RATIO = Decimal("2")
MAXIMUM_SIGNAL_BODY_PCT = Decimal("3")
MAXIMUM_VWAP_EXTENSION_PCT = Decimal("5")
MINIMUM_QUALITY_CHECKS = 4


class EntryQualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    value: Decimal | None = None
    rule: str


class EntryQualityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checks: tuple[EntryQualityCheck, ...]
    passed_count: int = Field(ge=0, le=5)
    quality_score: int = Field(ge=0, le=10)
    five_minute_trend_ready: bool
    five_minute_trend_bullish: bool
    passed: bool
    reasons: tuple[str, ...] = ()


def evaluate_entry_quality(
    *,
    gap_pct: Decimal,
    base_range_pct: Decimal | None,
    breakout_volume_ratio: Decimal | None,
    signal_body_pct: Decimal | None,
    vwap_extension_pct: Decimal | None,
    five_minute: IndicatorSnapshot,
) -> EntryQualityDecision:
    """Evaluate the frozen 4-of-5 anti-chase entry-quality hypothesis.

    The five quality checks are intentionally independent from the mandatory
    finalized 5m EMA9 trend prerequisite. Missing values fail their individual
    quality check; a missing 5m EMA9 trend fails the entire decision.
    """

    checks = (
        EntryQualityCheck(
            name="gap_strength",
            passed=gap_pct >= MINIMUM_GAP_PCT,
            value=gap_pct,
            rule="gap_pct >= 30",
        ),
        EntryQualityCheck(
            name="base_compression",
            passed=base_range_pct is not None and base_range_pct <= MAXIMUM_BASE_RANGE_PCT,
            value=base_range_pct,
            rule="base_range_pct <= 8",
        ),
        EntryQualityCheck(
            name="breakout_participation",
            passed=breakout_volume_ratio is not None and breakout_volume_ratio >= MINIMUM_BREAKOUT_VOLUME_RATIO,
            value=breakout_volume_ratio,
            rule="breakout_volume_ratio >= 2",
        ),
        EntryQualityCheck(
            name="anti_chase_candle",
            passed=signal_body_pct is not None and signal_body_pct <= MAXIMUM_SIGNAL_BODY_PCT,
            value=signal_body_pct,
            rule="signal_body_pct <= 3",
        ),
        EntryQualityCheck(
            name="vwap_extension",
            passed=vwap_extension_pct is not None and vwap_extension_pct <= MAXIMUM_VWAP_EXTENSION_PCT,
            value=vwap_extension_pct,
            rule="vwap_extension_pct <= 5",
        ),
    )
    passed_count = sum(check.passed for check in checks)
    trend_ready = (
        five_minute.ema9 is not None
        and five_minute.ema9_rising is not None
        and five_minute.price_above_ema9 is not None
    )
    trend_bullish = (
        trend_ready
        and five_minute.ema9_rising is True
        and five_minute.price_above_ema9 is True
    )

    reasons: list[str] = []
    if passed_count < MINIMUM_QUALITY_CHECKS:
        reasons.append("ENTRY_QUALITY_CONFLUENCE_LOW")
    if not trend_ready:
        reasons.append("ENTRY_5M_EMA9_UNWARMED")
    elif not trend_bullish:
        if five_minute.price_above_ema9 is not True:
            reasons.append("ENTRY_5M_PRICE_BELOW_EMA9")
        if five_minute.ema9_rising is not True:
            reasons.append("ENTRY_5M_EMA9_NOT_RISING")

    return EntryQualityDecision(
        checks=checks,
        passed_count=passed_count,
        quality_score=passed_count * 2,
        five_minute_trend_ready=trend_ready,
        five_minute_trend_bullish=trend_bullish,
        passed=passed_count >= MINIMUM_QUALITY_CHECKS and trend_bullish,
        reasons=tuple(reasons),
    )


__all__ = [
    "EntryQualityCheck",
    "EntryQualityDecision",
    "MAXIMUM_BASE_RANGE_PCT",
    "MAXIMUM_SIGNAL_BODY_PCT",
    "MAXIMUM_VWAP_EXTENSION_PCT",
    "MINIMUM_BREAKOUT_VOLUME_RATIO",
    "MINIMUM_GAP_PCT",
    "MINIMUM_QUALITY_CHECKS",
    "evaluate_entry_quality",
]
