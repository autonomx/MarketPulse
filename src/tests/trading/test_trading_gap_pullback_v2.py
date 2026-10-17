from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar
from app.apps.trading.strategies import evaluate_gap_pullback
from app.apps.trading.strategies.models import GapPullbackConfig


INSTRUMENT = "equity:NASDAQ:V2"
OPEN = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)  # 09:30 ET


def bar(index: int, open_: str, high: str, low: str, close: str, volume: str = "100") -> MarketBar:
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
        "binding_id": "fixture:V2",
        "previous_close": Decimal("8"),
        "premarket_price": Decimal("10.4"),
        "gap_pct": Decimal("30"),
        "premarket_volume": Decimal("20000"),
        "premarket_dollar_volume": Decimal("208000"),
        "tod_rvol": Decimal("4"),
        "market_cap": Decimal("50000000"),
        "float_shares": Decimal("5000000"),
        "spread_bps": Decimal("40"),
        "discovery_rank": 1,
    }
    payload.update(overrides)
    return GapperCandidate(**payload)


def v2_config(**overrides) -> GapPullbackConfig:
    payload = {
        "strategy_version": "2.0.0",
        "structure_interval": "1m",
        "execution_interval": "1m",
        "minimum_gap_pct": Decimal("20"),
        "minimum_price": Decimal("0.50"),
        "maximum_price": Decimal("20"),
        "minimum_premarket_dollar_volume": Decimal("100000"),
        "minimum_tod_rvol": Decimal("3"),
        "maximum_spread_bps": Decimal("150"),
        "require_catalyst_evidence": False,
        "pullback_min_pct": Decimal("8"),
        "pullback_max_pct": Decimal("25"),
        "higher_low_buffer_bps": Decimal("50"),
        "volume_lookback_bars": 5,
        "minimum_quality_score": 0,
        "v2_recovery_min_pct": Decimal("5"),
        "v2_second_pullback_min_pct": Decimal("2"),
        "v2_minimum_l1_to_b1_minutes": 4,
        "v2_maximum_l2_to_signal_minutes": 8,
        "v2_minimum_breakout_volume_ratio": Decimal("0"),
        "stop_buffer_bps": Decimal("15"),
        "reward_multiple": Decimal("1.5"),
        "entry_start_et": time(9, 35),
        "last_entry_et": time(11, 30),
    }
    payload.update(overrides)
    return GapPullbackConfig(**payload)


def v2_pattern() -> list[MarketBar]:
    # Premarket gap is the impulse. There is intentionally no +8% post-open
    # rally. L1=9.0, B1=10.0 four minutes later, L2=9.4, then direct B1/VWAP
    # breakout two minutes after L2.
    return [
        bar(0, "10.30", "10.40", "10.10", "10.20", "150"),
        bar(1, "10.20", "10.25", "9.60", "9.80", "130"),
        bar(2, "9.80", "9.85", "9.00", "9.20", "120"),
        bar(3, "9.20", "9.45", "9.10", "9.40", "100"),
        bar(4, "9.40", "9.65", "9.35", "9.60", "100"),
        bar(5, "9.60", "9.85", "9.55", "9.80", "110"),
        bar(6, "9.80", "10.00", "9.70", "9.90", "120"),
        bar(7, "9.90", "9.95", "9.65", "9.70", "90"),
        bar(8, "9.70", "9.80", "9.40", "9.50", "80"),
        bar(9, "9.50", "9.75", "9.45", "9.70", "90"),
        bar(10, "9.75", "10.40", "9.70", "10.30", "500"),
    ]


def test_v2_uses_premarket_gap_as_impulse_and_emits_direct_break() -> None:
    bars = v2_pattern()
    result = evaluate_gap_pullback(candidate(), bars, v2_config())

    assert max(item.high for item in bars) <= Decimal("10.4")
    assert result.state == "entry_ready"
    assert result.reason_code == "FAILED_SELLOFF_V2_TIMING_BREAK"
    assert result.signal is not None
    assert result.features.l1 == Decimal("9.00")
    assert result.features.b1 == Decimal("10.00")
    assert result.features.l2 == Decimal("9.40")
    assert result.features.l1_to_b1_minutes == 4
    assert result.features.l2_to_signal_minutes == 2
    assert result.signal.target_price > result.signal.entry_price
    assert "higher_low_confirmed" in result.transitions
    assert "vwap_reclaim" in result.transitions
    assert "lower_high_break" in result.transitions
    assert "breakout_hold" not in result.transitions


def test_v2_rejects_a_base_that_forms_faster_than_frozen_timing_gate() -> None:
    result = evaluate_gap_pullback(
        candidate(),
        v2_pattern(),
        v2_config(v2_minimum_l1_to_b1_minutes=5),
    )
    assert result.state == "rejected"
    assert result.reason_code == "V2_BASE_TOO_FAST"
    assert result.features.l1_to_b1_minutes == 4


def test_v2_rejects_a_breakout_that_resolves_too_slowly() -> None:
    result = evaluate_gap_pullback(
        candidate(),
        v2_pattern(),
        v2_config(v2_maximum_l2_to_signal_minutes=1),
    )
    assert result.state == "rejected"
    assert result.reason_code == "V2_RESOLUTION_TOO_SLOW"
    assert result.features.l2_to_signal_minutes == 2


def test_v2_uses_feature_specific_liquidity_gate_instead_of_global_data_flag() -> None:
    result = evaluate_gap_pullback(
        candidate(
            premarket_volume=Decimal("0"),
            premarket_dollar_volume=Decimal("0"),
            premarket_bar_count=0,
            tod_rvol=None,
            spread_bps=None,
            market_data_complete=False,
            data_quality_flags=(
                "PREMARKET_BARS_MISSING",
                "TOD_RVOL_MISSING",
                "SPREAD_MISSING",
            ),
        ),
        v2_pattern(),
        v2_config(),
    )
    assert result.state == "rejected"
    assert result.reason_code == "PREMARKET_DOLLAR_VOLUME_LOW"


def test_v2_prospective_evaluation_fails_closed_when_tod_rvol_is_missing() -> None:
    result = evaluate_gap_pullback(candidate(tod_rvol=None), v2_pattern(), v2_config())
    assert result.state == "rejected"
    assert result.reason_code == "TOD_RVOL_MISSING"


def test_v2_does_not_reemit_an_already_passed_breakout() -> None:
    bars = [*v2_pattern(), bar(11, "10.30", "10.45", "10.20", "10.35", "120")]
    result = evaluate_gap_pullback(candidate(), bars, v2_config())
    assert result.signal is None
    assert result.state == "lower_high_break"
    assert result.reason_code == "BREAKOUT_ALREADY_PASSED"


def test_v1_config_and_evaluator_remain_backward_compatible() -> None:
    legacy = GapPullbackConfig(strategy_version="1.1.0")
    assert legacy.v2_minimum_l1_to_b1_minutes == 4
    assert legacy.v2_maximum_l2_to_signal_minutes == 8

    # The v1 evaluator still requires its own post-open impulse semantics, so the
    # exact V2 pattern must not become an entry merely because V2 exists.
    result = evaluate_gap_pullback(candidate(), v2_pattern(), legacy)
    assert result.state != "entry_ready"
