from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.models import MarketBar
from app.apps.trading.paper import PaperExecutionPolicy
from app.apps.trading.strategies.models import GapPullbackConfig
from app.apps.trading.strategy_backtest import freeze_backtest_session, run_gap_pullback_backtest
from app.apps.trading.strategy_timeframes import proposal_priority, resample_final_bars


INSTRUMENT = "equity:NASDAQ:TF"
OPEN = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)


def minute_bar(
    index: int,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "100",
) -> MarketBar:
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


def candidate() -> GapperCandidate:
    return GapperCandidate(
        instrument_id=INSTRUMENT,
        binding_id="fixture:TF",
        previous_close=Decimal("8"),
        premarket_price=Decimal("10.4"),
        gap_pct=Decimal("30"),
        premarket_volume=Decimal("100000"),
        premarket_dollar_volume=Decimal("1040000"),
        tod_rvol=Decimal("3"),
        market_cap=Decimal("50000000"),
        float_shares=Decimal("5000000"),
        spread_bps=Decimal("40"),
        discovery_rank=1,
    )


def expand_structure_pattern() -> list[MarketBar]:
    structure = [
        ("10", "10.4", "9.9", "10.3", "100"),
        ("10.3", "11.2", "10.2", "11.0", "120"),
        ("11.0", "11.05", "10.5", "10.6", "80"),
        ("10.6", "10.7", "9.8", "10.0", "70"),
        ("10.0", "10.9", "9.95", "10.8", "80"),
        ("10.8", "11.1", "10.7", "11.0", "90"),
        ("10.9", "11.0", "10.4", "10.5", "80"),
        ("10.5", "10.7", "10.2", "10.3", "70"),
        ("10.3", "11.0", "10.25", "10.9", "80"),
        ("10.9", "11.8", "10.85", "11.7", "400"),
        ("11.75", "12.0", "11.6", "11.9", "1000"),
        ("11.9", "15.5", "11.8", "15.0", "1000"),
    ]
    output: list[MarketBar] = []
    for structure_index, (open_, high, low, close, volume) in enumerate(structure):
        per_minute_volume = str(Decimal(volume) / Decimal("5"))
        for offset in range(5):
            output.append(
                minute_bar(
                    structure_index * 5 + offset,
                    open_,
                    high,
                    low,
                    close,
                    per_minute_volume,
                )
            )
    return output


def test_resample_5m_uses_only_complete_final_buckets() -> None:
    bars = [
        minute_bar(0, "10", "11", "9", "10.2", "100"),
        minute_bar(1, "10.2", "11.2", "10", "10.5", "200"),
        minute_bar(2, "10.5", "10.8", "9.8", "10.1", "300"),
        minute_bar(3, "10.1", "10.6", "9.7", "10.0", "400"),
        minute_bar(4, "10", "10.9", "9.9", "10.8", "500"),
        minute_bar(5, "10.8", "11", "10.7", "10.9", "600"),
    ]

    resampled = resample_final_bars(bars, "5m")

    assert len(resampled) == 1
    assert resampled[0].interval == "5m"
    assert resampled[0].start_time == OPEN
    assert resampled[0].end_time == OPEN + timedelta(minutes=5)
    assert resampled[0].open == Decimal("10")
    assert resampled[0].high == Decimal("11.2")
    assert resampled[0].low == Decimal("9")
    assert resampled[0].close == Decimal("10.8")
    assert resampled[0].volume == Decimal("1500")



def test_resample_3m_uses_only_complete_final_buckets() -> None:
    bars = [
        minute_bar(0, "10", "10.5", "9.9", "10.2", "100"),
        minute_bar(1, "10.2", "10.6", "10.1", "10.4", "200"),
        minute_bar(2, "10.4", "10.8", "10.3", "10.7", "300"),
        minute_bar(3, "10.7", "10.9", "10.6", "10.8", "400"),
    ]

    resampled = resample_final_bars(bars, "3m")

    assert len(resampled) == 1
    assert resampled[0].interval == "3m"
    assert resampled[0].start_time == OPEN
    assert resampled[0].end_time == OPEN + timedelta(minutes=3)
    assert resampled[0].open == Decimal("10")
    assert resampled[0].high == Decimal("10.8")
    assert resampled[0].low == Decimal("9.9")
    assert resampled[0].close == Decimal("10.7")
    assert resampled[0].volume == Decimal("600")


def test_timeframe_config_rejects_execution_coarser_than_structure() -> None:
    with pytest.raises(ValueError, match="execution_interval cannot be coarser"):
        GapPullbackConfig(structure_interval="1m", execution_interval="5m")


def test_simultaneous_portfolio_priority_prefers_quality_before_scan_rank() -> None:
    observed = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    high_quality = proposal_priority(
        observed_at=observed,
        quality_score=9,
        discovery_rank=20,
        instrument_id="equity:NASDAQ:HIGH",
    )
    low_quality = proposal_priority(
        observed_at=observed,
        quality_score=7,
        discovery_rank=1,
        instrument_id="equity:NASDAQ:LOW",
    )
    earlier_low_quality = proposal_priority(
        observed_at=observed - timedelta(minutes=1),
        quality_score=7,
        discovery_rank=1,
        instrument_id="equity:NASDAQ:EARLY",
    )

    assert earlier_low_quality < high_quality < low_quality


def test_backtest_uses_5m_structure_and_next_1m_execution_bar() -> None:
    universe = freeze_gapper_universe(
        universe_id="tf-2026-08-18",
        session_date=date(2026, 8, 18),
        evaluation_time=datetime(2026, 8, 18, 13, 20, tzinfo=timezone.utc),
        discovery_source="import",
        candidates=[candidate()],
    )
    bars = expand_structure_pattern()
    dataset = freeze_backtest_session(
        session_date=date(2026, 8, 18),
        universe=universe,
        bars_by_instrument={INSTRUMENT: bars},
    )
    config = GapPullbackConfig(
        strategy_version="1.1.0",
        structure_interval="5m",
        execution_interval="1m",
        pivot_left_bars=1,
        pivot_right_bars=1,
        volume_lookback_bars=5,
        breakout_volume_ratio=Decimal("1.25"),
        entry_start_et=time(9, 30),
    )
    result = run_gap_pullback_backtest(
        dataset,
        config,
        PaperExecutionPolicy(
            slippage_bps=Decimal("10"),
            max_volume_participation_pct=Decimal("1"),
            latency_ms=0,
        ),
        assumed_spread_bps=Decimal("40"),
        max_hold_minutes=90,
    )

    assert result.strategy_version == "1.1.0"
    assert result.summary.trigger_count == 1
    assert result.summary.trade_count == 1
    trade = result.trades[0]
    assert trade.structure_interval == "5m"
    assert trade.execution_interval == "1m"
    assert trade.trigger_bar_index == 49
    assert trade.entry_bar_index == 50
    assert trade.entry_time == bars[50].start_time
    # This synthetic research/backtest fixture deterministically scores 8/10.
    assert trade.quality_score == 8
