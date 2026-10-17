from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_stoch_rsi_5m import StochRsi5mSnapshot, StochRsi5mTrade
from app.apps.trading import strategy_stoch_rsi_5m_early_single_research as research


START = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)  # 09:30 ET


def _bar(
    index: int,
    *,
    interval: str = "5m",
    open_: str = "10",
    high: str = "10.2",
    low: str = "9.8",
    close: str = "10",
    volume: str = "1000",
) -> MarketBar:
    minutes = 1 if interval == "1m" else 5
    start = START + timedelta(minutes=minutes * index)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval=interval,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=minutes),
    )


def _snapshot(
    bars: list[MarketBar],
    *,
    arm_index: int = 1,
    signal_index: int = 3,
    entry_index: int = 4,
    exit_index: int = 8,
) -> StochRsi5mSnapshot:
    trade = StochRsi5mTrade(
        oversold_arm_time=bars[arm_index].end_time,
        momentum_cross_time=bars[min(arm_index + 1, signal_index)].end_time,
        entry_signal_time=bars[signal_index].end_time,
        entry_time=bars[entry_index].start_time,
        entry_price=bars[entry_index].open,
        exit_signal_time=bars[exit_index - 1].end_time,
        exit_time=bars[exit_index].start_time,
        exit_price=bars[exit_index].open,
        exit_reason_code="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA",
        return_pct=(bars[exit_index].open - bars[entry_index].open)
        / bars[entry_index].open
        * Decimal("100"),
    )
    return StochRsi5mSnapshot(
        state="exited",
        reason_code=trade.exit_reason_code,
        session_date="2026-09-10",
        entry_time=trade.entry_time,
        entry_price=trade.entry_price,
        exit_time=trade.exit_time,
        exit_price=trade.exit_price,
        return_pct=trade.return_pct,
        trades=(trade,),
    )


def test_early_failure_grid_is_independent_and_complete() -> None:
    assert len(research.EARLY_FAILURE_ARM_SPECS) == 24
    assert research.EARLY_FAILURE_ARM_SPECS["early_failure_5m_entry_any"] == (
        5,
        "entry",
        None,
    )
    assert research.EARLY_FAILURE_ARM_SPECS["early_failure_15m_vwap_mfe3"] == (
        15,
        "vwap",
        Decimal("3"),
    )


def test_no_entry_1030_1100_rejects_only_inside_window(monkeypatch) -> None:
    bars = [_bar(i) for i in range(20)]
    blocked = _snapshot(bars, arm_index=9, signal_index=11, entry_index=12, exit_index=16)
    monkeypatch.setattr(research, "_baseline_trade", lambda bars, active: blocked)

    result = research.evaluate_stoch_rsi_5m_early_single_research_arm(
        bars,
        "no_entry_1030_1100",
    )

    assert result.trades == ()
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_NO_ENTRY_1030_1100"


def test_one_minute_hard_stop_preserves_5m_entry_and_limits_loss(monkeypatch) -> None:
    bars = [_bar(i) for i in range(12)]
    bars[4] = _bar(4, open_="10", high="10.1", low="9.8", close="9.9")
    bars[8] = _bar(8, open_="8.5", high="8.6", low="8.4", close="8.5")
    snapshot = _snapshot(bars)
    monkeypatch.setattr(research, "_baseline_trade", lambda bars, active: snapshot)
    one_minute = [_bar(i, interval="1m") for i in range(60)]
    one_minute[21] = _bar(
        21,
        interval="1m",
        open_="9.85",
        high="9.86",
        low="9.65",
        close="9.7",
    )

    result = research.evaluate_stoch_rsi_5m_early_single_research_arm(
        bars,
        "hard_stop_3pct",
        one_minute_bars=one_minute,
    )

    trade = result.trades[0]
    assert trade.entry_time == snapshot.trades[0].entry_time
    assert trade.entry_price == Decimal("10")
    assert trade.exit_price == Decimal("9.7")
    assert trade.return_pct == Decimal("-3")


def test_atr_stop_uses_only_pre_entry_one_minute_atr(monkeypatch) -> None:
    bars = [_bar(i) for i in range(12)]
    snapshot = _snapshot(bars)
    monkeypatch.setattr(research, "_baseline_trade", lambda bars, active: snapshot)
    one_minute = [_bar(i, interval="1m") for i in range(60)]
    monkeypatch.setattr(
        research,
        "average_true_range",
        lambda highs, lows, closes, period: [Decimal("0.40")],
    )

    stop = research._atr_stop_price(one_minute, snapshot.trades[0])

    assert stop == Decimal("9.20")


def test_vwap_reclaim_delays_below_vwap_entry(monkeypatch) -> None:
    bars = [
        _bar(0, open_="10", high="10.1", low="9.9", close="10"),
        _bar(1, open_="10", high="10.1", low="9.9", close="10"),
        _bar(2, open_="10", high="10.1", low="9.9", close="10"),
        _bar(3, open_="10", high="10.1", low="9.9", close="10"),
        _bar(4, open_="9.50", high="10.4", low="9.4", close="10.30"),
        _bar(5, open_="10.35", high="10.5", low="10.2", close="10.4"),
        _bar(6, open_="10.4", high="10.6", low="10.3", close="10.5"),
        _bar(7, open_="10.5", high="10.7", low="10.4", close="10.6"),
        _bar(8, open_="10.6", high="10.7", low="10.5", close="10.6"),
    ]
    snapshot = _snapshot(bars)
    monkeypatch.setattr(research, "_baseline_trade", lambda bars, active: snapshot)

    result = research.evaluate_stoch_rsi_5m_early_single_research_arm(
        bars,
        "vwap_reclaim_1bar",
    )

    trade = result.trades[0]
    assert trade.entry_time == bars[5].start_time
    assert trade.entry_price == Decimal("10.35")


def test_early_failure_close_below_entry_works_without_vwap_or_mfe_stack(monkeypatch) -> None:
    bars = [_bar(i) for i in range(12)]
    bars[4] = _bar(4, open_="10", high="10.2", low="9.7", close="9.8")
    bars[5] = _bar(5, open_="9.75", high="9.9", low="9.6", close="9.7")
    snapshot = _snapshot(bars)
    monkeypatch.setattr(research, "_baseline_trade", lambda bars, active: snapshot)

    result = research.evaluate_stoch_rsi_5m_early_single_research_arm(
        bars,
        "early_failure_5m_entry_any",
    )

    trade = result.trades[0]
    assert trade.exit_time == bars[5].start_time
    assert trade.exit_price == Decimal("9.75")
    assert "EARLY_FAILURE_5M_ENTRY_MFE_ANY" in trade.exit_reason_code


def test_failed_selloff_pattern_detects_undercut_and_reclaim() -> None:
    bars = [
        _bar(0, low="10.0", close="10.2"),
        _bar(1, low="10.0", close="10.1"),
        _bar(2, low="10.0", close="10.05"),
        _bar(3, low="9.90", close="10.08"),
        _bar(4, low="10.0", close="10.2"),
    ]

    confirmation = research._failed_selloff_confirmation(
        bars,
        session_start_index=0,
        arm_index=3,
        signal_index=3,
        stop_index=4,
    )

    assert confirmation == 3


def test_requested_pattern_arms_are_all_registered(monkeypatch) -> None:
    assert research.PATTERN_ARMS == {
        "failed_selloff",
        "higher_low",
        "lower_high_break",
        "double_bottom",
        "volume_exhaustion",
        "reversal_structure_v1",
        "reversal_structure_aggressive_v1",
    }

    bars = [
        _bar(0, low="10.0", close="10.1"),
        _bar(1, low="10.0", close="10.05"),
        _bar(2, low="9.9", close="10.0"),
        _bar(3, high="10.4", low="9.8", close="10.2"),
        _bar(4, open_="10.25", high="10.5", low="10.1", close="10.4"),
        _bar(5, open_="10.4", high="10.7", low="10.2", close="10.5"),
        _bar(6, open_="10.5", high="10.8", low="10.3", close="10.7"),
        _bar(7, open_="10.7", high="10.9", low="10.5", close="10.8"),
        _bar(8, open_="10.8", high="11", low="10.7", close="10.9"),
    ]
    snapshot = _snapshot(bars, arm_index=2, signal_index=3, entry_index=4, exit_index=8)
    monkeypatch.setattr(research, "_baseline_trade", lambda bars, active: snapshot)

    for arm in sorted(research.PATTERN_ARMS):
        result = research.evaluate_stoch_rsi_5m_early_single_research_arm(
            bars,
            arm,
        )
        assert result.execution_authority is False
