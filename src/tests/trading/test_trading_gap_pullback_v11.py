from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar
from app.apps.trading.strategies.gap_pullback import evaluate_gap_pullback
from app.apps.trading.strategies.models import GapPullbackConfig


INSTRUMENT = "equity:NASDAQ:V11"
OPEN = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)


def bar(index: int, open_: str, high: str, low: str, close: str, volume: str) -> MarketBar:
    start = OPEN + timedelta(minutes=index)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=1),
    )


def candidate(**overrides) -> GapperCandidate:
    payload = {
        "instrument_id": INSTRUMENT,
        "binding_id": "fixture:V11",
        "previous_close": Decimal("8"),
        "premarket_price": Decimal("10.4"),
        "gap_pct": Decimal("30"),
        "premarket_volume": Decimal("1200000"),
        "premarket_dollar_volume": Decimal("12480000"),
        "tod_rvol": Decimal("6"),
        "market_cap": Decimal("80000000"),
        "float_shares": Decimal("8000000"),
        "spread_bps": Decimal("40"),
        "catalyst_evidence_ids": ("ev-current-catalyst",),
        "dilution_flags": (),
        "discovery_rank": 1,
    }
    payload.update(overrides)
    return GapperCandidate(**payload)


def strict_config(**overrides) -> GapPullbackConfig:
    payload = {
        "strategy_version": "1.1.0",
        "minimum_gap_pct": Decimal("20"),
        "minimum_price": Decimal("0.50"),
        "maximum_price": Decimal("20"),
        "minimum_premarket_dollar_volume": Decimal("10000000"),
        "minimum_tod_rvol": Decimal("5"),
        "maximum_spread_bps": Decimal("150"),
        "preferred_float_min_shares": Decimal("2000000"),
        "preferred_float_max_shares": Decimal("30000000"),
        "float_preference_mode": "score",
        "require_catalyst_evidence": True,
        "reject_dilution_flags": (
            "registered_offering",
            "atm",
            "warrants",
            "convertible",
            "equity_line",
        ),
        "opening_impulse_min_pct": Decimal("8"),
        "pullback_min_pct": Decimal("15"),
        "pullback_max_pct": Decimal("55"),
        "pullback_volume_max_ratio": Decimal("0.70"),
        "higher_low_buffer_bps": Decimal("20"),
        "breakout_volume_ratio": Decimal("1.25"),
        "pivot_left_bars": 1,
        "pivot_right_bars": 1,
        "volume_lookback_bars": 5,
        "require_breakout_hold": True,
        "breakout_hold_bars": 1,
        "breakout_hold_tolerance_bps": Decimal("25"),
        "minimum_quality_score": 7,
        "entry_start_et": time(9, 30),
    }
    payload.update(overrides)
    return GapPullbackConfig(**payload)


def quality_pattern(*, heavy_pullback: bool = False) -> list[MarketBar]:
    red = "1200" if heavy_pullback else "250"
    return [
        bar(0, "10.00", "10.50", "9.90", "10.40", "1000"),
        bar(1, "10.40", "12.00", "10.30", "11.80", "1200"),
        bar(2, "11.80", "11.90", "10.80", "11.00", red),
        bar(3, "11.00", "11.10", "9.40", "9.60", red),
        bar(4, "9.60", "10.40", "9.50", "10.20", "300"),
        bar(5, "10.20", "10.80", "10.10", "10.60", "350"),
        bar(6, "10.60", "10.70", "9.80", "10.00", red),
        bar(7, "10.00", "10.30", "9.70", "9.90", red),
        bar(8, "9.90", "10.50", "9.80", "10.40", "250"),
        bar(9, "10.40", "11.40", "10.30", "11.20", "1000"),
        bar(10, "11.20", "11.50", "10.85", "11.10", "400"),
    ]


def test_v11_requires_fresh_catalyst_evidence() -> None:
    result = evaluate_gap_pullback(
        candidate(catalyst_evidence_ids=()),
        quality_pattern(),
        strict_config(),
    )
    assert result.state == "rejected"
    assert result.reason_code == "CATALYST_EVIDENCE_REQUIRED"


def test_reconstructed_data_can_explicitly_allow_missing_tod_rvol() -> None:
    missing_rvol = candidate(tod_rvol=None)

    strict_result = evaluate_gap_pullback(missing_rvol, [], strict_config())
    assert strict_result.reason_code == "TOD_RVOL_MISSING"

    reconstructed_result = evaluate_gap_pullback(
        missing_rvol,
        [],
        strict_config(allow_missing_tod_rvol=True),
    )
    assert reconstructed_result.state == "qualified_gap"


def test_v11_rejects_configured_supply_risk() -> None:
    result = evaluate_gap_pullback(
        candidate(dilution_flags=("atm",)),
        quality_pattern(),
        strict_config(),
    )
    assert result.state == "rejected"
    assert result.reason_code == "DILUTION_SUPPLY_RISK"
    assert result.features.supply_score == 0


def test_v11_can_hard_require_preferred_float() -> None:
    result = evaluate_gap_pullback(
        candidate(float_shares=Decimal("50000000")),
        quality_pattern(),
        strict_config(float_preference_mode="require"),
    )
    assert result.state == "rejected"
    assert result.reason_code == "FLOAT_OUTSIDE_REQUIRED_RANGE"


def test_v11_rejects_heavy_pullback_selling_volume() -> None:
    result = evaluate_gap_pullback(
        candidate(),
        quality_pattern(heavy_pullback=True),
        strict_config(),
    )
    assert result.state == "rejected"
    assert result.reason_code == "PULLBACK_SELLING_VOLUME_TOO_HIGH"
    assert result.features.pullback_volume_ratio is not None
    assert result.features.pullback_volume_ratio > Decimal("0.70")


def test_v11_waits_for_breakout_hold_then_emits_high_quality_entry() -> None:
    bars = quality_pattern()
    before_hold = evaluate_gap_pullback(candidate(), bars[:10], strict_config())
    assert before_hold.state == "lower_high_break"
    assert before_hold.reason_code == "WAITING_FOR_BREAKOUT_HOLD"

    confirmed = evaluate_gap_pullback(candidate(), bars, strict_config())
    assert confirmed.state == "entry_ready"
    assert confirmed.signal is not None
    assert confirmed.signal.quality_score >= 7
    assert confirmed.features.quality_score >= 7
    assert confirmed.features.pullback_volume_ratio is not None
    assert confirmed.features.pullback_volume_ratio <= Decimal("0.70")
    assert "higher_low_confirmed" in confirmed.transitions
    assert "vwap_reclaim" in confirmed.transitions
    assert "lower_high_break" in confirmed.transitions
    assert "breakout_hold" in confirmed.transitions


def test_v11_quality_threshold_is_configurable() -> None:
    result = evaluate_gap_pullback(
        candidate(float_shares=Decimal("50000000")),
        quality_pattern(),
        strict_config(float_preference_mode="score", minimum_quality_score=10),
    )
    assert result.state == "rejected"
    assert result.reason_code == "QUALITY_SCORE_BELOW_MINIMUM"
    assert result.features.quality_score == 9
