from decimal import Decimal

from app.apps.trading.indicator_signals import IndicatorSnapshot
from app.apps.trading.strategy_entry_quality import evaluate_entry_quality


def _five(*, ready: bool = True, above: bool = True, rising: bool = True) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        interval="5m",
        bar_count=10 if ready else 3,
        close=Decimal("10"),
        ema9=Decimal("9.8") if ready else None,
        ema9_change=Decimal("0.1") if ready else None,
        price_above_ema9=above if ready else None,
        ema9_rising=rising if ready else None,
    )


def test_entry_quality_passes_four_of_five_with_bullish_five_minute_trend():
    decision = evaluate_entry_quality(
        gap_pct=Decimal("35"),
        base_range_pct=Decimal("7"),
        breakout_volume_ratio=Decimal("2.5"),
        signal_body_pct=Decimal("2"),
        vwap_extension_pct=Decimal("7"),  # one failed quality check
        five_minute=_five(),
    )

    assert decision.passed is True
    assert decision.passed_count == 4
    assert decision.quality_score == 8
    assert decision.reasons == ()


def test_entry_quality_rejects_three_of_five_even_when_trend_is_bullish():
    decision = evaluate_entry_quality(
        gap_pct=Decimal("24"),
        base_range_pct=Decimal("9"),
        breakout_volume_ratio=Decimal("2.2"),
        signal_body_pct=Decimal("2"),
        vwap_extension_pct=Decimal("4"),
        five_minute=_five(),
    )

    assert decision.passed is False
    assert decision.passed_count == 3
    assert "ENTRY_QUALITY_CONFLUENCE_LOW" in decision.reasons


def test_entry_quality_requires_warmed_five_minute_ema9():
    decision = evaluate_entry_quality(
        gap_pct=Decimal("40"),
        base_range_pct=Decimal("5"),
        breakout_volume_ratio=Decimal("3"),
        signal_body_pct=Decimal("1"),
        vwap_extension_pct=Decimal("2"),
        five_minute=_five(ready=False),
    )

    assert decision.passed_count == 5
    assert decision.passed is False
    assert decision.five_minute_trend_ready is False
    assert decision.reasons == ("ENTRY_5M_EMA9_UNWARMED",)


def test_entry_quality_rejects_bearish_five_minute_trend():
    decision = evaluate_entry_quality(
        gap_pct=Decimal("40"),
        base_range_pct=Decimal("5"),
        breakout_volume_ratio=Decimal("3"),
        signal_body_pct=Decimal("1"),
        vwap_extension_pct=Decimal("2"),
        five_minute=_five(above=False, rising=False),
    )

    assert decision.passed is False
    assert "ENTRY_5M_PRICE_BELOW_EMA9" in decision.reasons
    assert "ENTRY_5M_EMA9_NOT_RISING" in decision.reasons


def test_entry_quality_anti_chase_is_an_upper_bound_not_a_green_candle_requirement():
    decision = evaluate_entry_quality(
        gap_pct=Decimal("35"),
        base_range_pct=Decimal("7"),
        breakout_volume_ratio=Decimal("2.1"),
        signal_body_pct=Decimal("-0.25"),
        vwap_extension_pct=Decimal("3"),
        five_minute=_five(),
    )

    anti_chase = next(check for check in decision.checks if check.name == "anti_chase_candle")
    assert anti_chase.passed is True
    assert decision.passed is True
